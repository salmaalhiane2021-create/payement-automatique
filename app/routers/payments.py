"""
Paiement électronique sécurisé par l'IA.

Déroulé en trois appels
-----------------------
1. `POST /api/payments/quote`  : analyse de risque → score, décision, facteurs.
   Si la décision n'est pas « block », un **code à usage unique** est généré
   (60 secondes, usage unique, vérifié côté serveur).
2. `GET  /api/payments/challenge/{id}` : état du défi (compte à rebours).
3. `POST /api/payments/confirm` : vérification du code (+ code PIN si le risque
   est élevé) puis exécution de l'opération simulée.

Aucune donnée de carte n'est demandée ni transmise : le prototype ne simule
aucun prélèvement réel.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db, now_utc
from app.deps import client_ip, get_current_user
from app.models import Device, PaymentChallenge, Transaction, User
from app.schemas import (
    ChallengeStatusOut,
    PaymentConfirmRequest,
    PaymentQuoteRequest,
    PaymentQuoteResponse,
    PaymentResultOut,
    RiskFactor,
)
from app.services import geo
from app.services.alerts import (
    alert_for_blocked_payment,
    alert_for_high_anomaly,
    log_action,
)
from app.services.behavior import refresh_profile, to_local
from app.services.ledger import accounts_of
from app.services.otp import (
    attempts_left,
    create_challenge,
    masked_phone,
    purge_expired,
    seconds_remaining,
    verify_challenge,
)
from app.services.risk_engine import assess_payment

router = APIRouter(prefix="/api/payments", tags=["paiement sécurisé"])


# ---------------------------------------------------------------------------
# Contexte et scénarios de démonstration
# ---------------------------------------------------------------------------
@router.get("/context")
def payment_context(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Informations nécessaires au formulaire de paiement."""
    from app.seed import MERCHANTS  # catalogue de commerçants (données de démo)

    devices = list(db.scalars(select(Device).where(Device.user_id == user.id).order_by(Device.last_seen.desc())).all())
    checking, _ = accounts_of(db, user)
    categories = sorted(set(MERCHANTS.keys()) | {"Shopping", "Crypto", "Jeux d'argent", "Transfert international", "Autres"})

    return {
        "devices": [
            {
                "id": device.id,
                "label": device.label,
                "platform": device.platform,
                "fingerprint": device.fingerprint,
                "is_trusted": device.is_trusted,
            }
            for device in devices
        ],
        "cities": sorted(geo.CITY_COORDS.keys()),
        # Pays associé à chaque ville : permet au formulaire d'envoyer le bon
        # contexte géographique (indispensable pour tester les scénarios).
        "city_countries": {city: entry[2] for city, entry in geo.CITY_COORDS.items()},
        "categories": categories,
        "balance": round(float(checking.balance if checking else 0.0), 2),
        "otp_ttl_seconds": settings.otp_ttl_seconds,
        "otp_max_attempts": settings.otp_max_attempts,
        "masked_phone": masked_phone(user.phone),
    }


@router.get("/scenarios")
def demo_scenarios(user: User = Depends(get_current_user)) -> dict:
    """
    Scénarios prêts à l'emploi pour la démonstration.

    Ils n'ont rien de magique : ce sont simplement des formulaires préremplis.
    C'est le moteur de risque qui décide ensuite.

    Chaque scénario fige l'heure simulée (`simulated_hour`) afin que le
    résultat soit reproductible quel que soit le moment de la démonstration.
    """
    return {
        "scenarios": [
            {
                "code": "habituel",
                "title": "Achat habituel",
                "description": (
                    "Boulangerie à midi, petit montant, appareil de confiance : le paiement passe "
                    "après un simple code à usage unique."
                ),
                "expected": "accept",
                "payload": {
                    "merchant": "Boulangerie Maison Blanc",
                    "amount": 7.4,
                    "category": "Alimentation",
                    "city": user.city or "Paris",
                    "country": "France",
                    "channel": "carte",
                    "simulated_hour": 12,
                    "device_label": "Appareil habituel",
                    "use_known_device": True,
                },
            },
            {
                "code": "montant",
                "title": "Montant inhabituel",
                "description": (
                    "850 € chez un marchand d'électronique (simulé à 15h) : bien au-dessus de vos habitudes, "
                    "le moteur exige la vérification renforcée (code à usage unique + code PIN)."
                ),
                "expected": "verify",
                "payload": {
                    "merchant": "TechDeal Store",
                    "amount": 850.0,
                    "category": "Shopping",
                    "city": user.city or "Paris",
                    "country": "France",
                    "channel": "en_ligne",
                    "simulated_hour": 15,
                    "device_label": "Appareil habituel",
                    "use_known_device": True,
                },
            },
            {
                "code": "appareil",
                "title": "Appareil inconnu",
                "description": (
                    "679 € depuis un navigateur jamais vu sur le compte (simulé à 10h) : un signal critique "
                    "impose la double validation, même si le montant reste plausible."
                ),
                "expected": "verify",
                "payload": {
                    "merchant": "PremiumGadgets Online",
                    "amount": 679.0,
                    "category": "Shopping",
                    "city": user.city or "Paris",
                    "country": "France",
                    "channel": "en_ligne",
                    "simulated_hour": 10,
                    "device_label": "Navigateur non identifié",
                    "use_known_device": False,
                },
            },
            {
                "code": "voyage",
                "title": "Déplacement impossible",
                "description": (
                    "Un paiement local est d'abord exécuté (simulé à 21h, dans votre ville), puis un retrait "
                    "de 620 € à Lagos deux minutes plus tard : le déplacement est physiquement impossible."
                ),
                "expected": "block",
                "pre_payment": {
                    "merchant": "Boulangerie Maison Blanc",
                    "amount": 6.2,
                    "category": "Alimentation",
                    "city": user.city or "Paris",
                    "country": "France",
                    "channel": "carte",
                    "simulated_hour": 21,
                    "simulated_minute": 0,
                    "use_known_device": True,
                },
                "payload": {
                    "merchant": "Western Union",
                    "amount": 620.0,
                    "category": "Transfert international",
                    "city": "Lagos",
                    "country": "Nigéria",
                    "channel": "virement",
                    "simulated_hour": 21,
                    "simulated_minute": 4,
                    "device_label": "Terminal pirate",
                    "use_known_device": False,
                },
            },
            {
                "code": "nuit",
                "title": "Achat nocturne",
                "description": (
                    "380 € sur un site de jeux d'argent à 3h du matin : heure atypique et catégorie sensible, "
                    "deux signaux qui déclenchent la vérification renforcée."
                ),
                "expected": "verify",
                "payload": {
                    "merchant": "iGaming Pro",
                    "amount": 380.0,
                    "category": "Jeux d'argent",
                    "city": user.city or "Paris",
                    "country": "France",
                    "channel": "en_ligne",
                    "simulated_hour": 3,
                    "device_label": "Appareil habituel",
                    "use_known_device": True,
                },
            },
        ]
    }


# ---------------------------------------------------------------------------
# 1. Analyse de risque et génération du code à usage unique
# ---------------------------------------------------------------------------
@router.post("/quote", response_model=PaymentQuoteResponse)
def quote_payment(
    payload: PaymentQuoteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaymentQuoteResponse:
    """Analyse le risque du paiement puis remet un code à usage unique (60 s)."""
    from app.services.behavior import get_profile

    purge_expired(db)  # hygiène : les défis expirés ne doivent plus être utilisables

    # Horodatage de l'opération (heure locale simulable pour la démonstration).
    # L'heure simulée est ramenée à sa dernière occurrence passée : une
    # simulation à 3h du matin correspond ainsi à « cette nuit », ce qui garde
    # les analyses de vélocité et d'historique cohérentes.
    ts = now_utc()
    if payload.simulated_hour is not None:
        local = to_local(ts).replace(
            hour=payload.simulated_hour,
            minute=int(payload.simulated_minute or 0),
            second=0,
            microsecond=0,
        )
        if local > to_local(ts):
            local -= timedelta(days=1)
        ts = _local_to_utc(local)

    country = payload.country or geo.country_for(payload.city)

    # Coordonnées : celles fournies, sinon celles de la ville connue. Elles sont
    # enregistrées avec l'opération, ce qui permet ensuite de détecter les
    # déplacements impossibles entre deux paiements.
    latitude, longitude = payload.latitude, payload.longitude
    if latitude is None or longitude is None:
        coords = geo.coords_for(payload.city, country)
        if coords:
            latitude, longitude = coords

    payment = {
        "merchant": payload.merchant,
        "amount": round(float(payload.amount), 2),
        "category": payload.category,
        "city": payload.city,
        "country": country,
        "channel": payload.channel,
        "device_fingerprint": payload.device_fingerprint or "",
        "device_label": payload.device_label,
        "latitude": latitude,
        "longitude": longitude,
        "ts": ts,
        "note": payload.note,
    }

    profile = get_profile(db, user)
    assessment = assess_payment(db, user, payment, profile)

    log_action(
        db,
        action="paiement_analyse",
        user_id=user.id,
        status="ok" if assessment.decision != "block" else "bloque",
        detail=(
            f"{payment['amount']:.2f} € vers {payment['merchant']} — score {assessment.score}/100 "
            f"({assessment.decision})"
        ),
        ip_address=client_ip(request),
    )

    # --- Paiement bloqué : aucune suite possible --------------------------
    if assessment.decision == "block":
        alert_for_blocked_payment(db, user.id, payment, assessment)
        return PaymentQuoteResponse(
            decision="block",
            decision_label=assessment.decision_label,
            risk_score=assessment.score,
            anomaly_score=assessment.anomaly_score,
            factors=[RiskFactor(**factor) for factor in assessment.factors],
            summary=assessment.summary,
            challenge_id=None,
            require_pin=False,
            masked_phone=masked_phone(user.phone),
        )

    # --- Génération du code à usage unique ---------------------------------
    challenge_payload = {
        "purpose": "paiement",
        "ts": ts.isoformat(),
        "amount": payment["amount"],
        "merchant": payment["merchant"],
        "category": payment["category"],
        "city": payment["city"],
        "country": payment["country"],
        "channel": payment["channel"],
        "device_fingerprint": payment["device_fingerprint"],
        "device_label": payment["device_label"],
        "latitude": payment["latitude"],
        "longitude": payment["longitude"],
        "risk_score": assessment.score,
        "anomaly_score": assessment.anomaly_score,
        "decision": assessment.decision,
        "facteurs": assessment.factors,
        "explication": assessment.summary,
    }
    challenge, code = create_challenge(
        db,
        user=user,
        payload=challenge_payload,
        risk_score=assessment.score,
        decision=assessment.decision,
        require_pin=assessment.require_pin,
    )

    log_action(
        db,
        action="code_otp_emis",
        user_id=user.id,
        detail=f"Défi #{challenge.id} valable {settings.otp_ttl_seconds} s (décision : {assessment.decision}).",
        ip_address=client_ip(request),
    )

    return PaymentQuoteResponse(
        decision=assessment.decision,  # type: ignore[arg-type]
        decision_label=assessment.decision_label,
        risk_score=assessment.score,
        anomaly_score=assessment.anomaly_score,
        factors=[RiskFactor(**factor) for factor in assessment.factors],
        summary=assessment.summary,
        challenge_id=challenge.id,
        require_pin=assessment.require_pin,
        otp_ttl_seconds=settings.otp_ttl_seconds,
        # En production, ce champ n'existe pas : le code partirait par SMS.
        simulation_code=None if settings.is_production else code,
        masked_phone=masked_phone(user.phone),
    )


# ---------------------------------------------------------------------------
# 2. État du défi (compte à rebours côté client)
# ---------------------------------------------------------------------------
@router.get("/challenge/{challenge_id}", response_model=ChallengeStatusOut)
def challenge_status(
    challenge_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChallengeStatusOut:
    """Secondes restantes, tentatives restantes et état du défi."""
    challenge = _load_challenge(db, user, challenge_id)
    return ChallengeStatusOut(
        challenge_id=challenge.id,
        expires_at=challenge.expires_at,
        seconds_remaining=seconds_remaining(challenge),
        attempts_left=attempts_left(challenge),
        consumed=challenge.consumed_at is not None,
        expired=seconds_remaining(challenge) == 0,
        decision=challenge.decision,
        require_pin=challenge.require_pin,
        amount=float((challenge.payload or {}).get("amount", 0.0)),
        merchant=str((challenge.payload or {}).get("merchant", "")),
    )


@router.post("/challenge/{challenge_id}/cancel")
def cancel_challenge(
    challenge_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Annule un défi en cours (l'utilisateur renonce au paiement)."""
    challenge = _load_challenge(db, user, challenge_id)
    if challenge.consumed_at is None:
        challenge.consumed_at = now_utc()
        db.commit()
    log_action(db, "paiement_annule", user.id, "ok", f"Défi #{challenge.id} annulé.", client_ip(request))
    return {"status": "ok", "message": "Paiement annulé."}


# ---------------------------------------------------------------------------
# 3. Validation du code et exécution du paiement
# ---------------------------------------------------------------------------
@router.post("/confirm", response_model=PaymentResultOut)
def confirm_payment(
    payload: PaymentConfirmRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaymentResultOut:
    """Vérifie le code à usage unique puis exécute le paiement simulé."""
    challenge = _load_challenge(db, user, payload.challenge_id)
    ip = client_ip(request)

    # --- Vérification côté serveur du code (+ PIN si nécessaire) ----------
    valid, error_code, message = verify_challenge(db, challenge, payload.code, payload.pin)
    if not valid:
        log_action(
            db,
            action="paiement_refuse",
            user_id=user.id,
            status="echec",
            detail=f"Défi #{challenge.id} : {error_code} — {message}",
            ip_address=ip,
        )
        if error_code in {"trop_dessais", "code_invalide", "pin_invalide"}:
            from app.services.alerts import create_alert

            create_alert(
                db,
                user_id=user.id,
                kind="securite",
                severity="eleve" if error_code == "trop_dessais" else "moyen",
                title="Tentative de validation échouée",
                message=(
                    f"Un code de paiement incorrect a été saisi pour l'opération vers "
                    f"« {(challenge.payload or {}).get('merchant')} » ({message})"
                ),
                details={"defi": challenge.id, "erreur": error_code},
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    # --- Exécution de l'opération simulée ---------------------------------
    data = challenge.payload or {}
    amount = round(float(data.get("amount", 0.0)), 2)
    checking, _ = accounts_of(db, user)

    if checking is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun compte courant associé.")

    # Contrôle de solde au moment de l'exécution (il a pu changer depuis l'analyse)
    if amount > float(checking.balance):
        log_action(db, "paiement_refuse", user.id, "bloque", "Solde insuffisant au moment du débit.", ip)
        return PaymentResultOut(
            status="blocked",
            message=(
                f"Paiement refusé : solde insuffisant ({checking.balance:.2f} € disponibles "
                f"pour {amount:.2f} € demandés)."
            ),
            risk_score=float(data.get("risk_score", 0.0)),
            anomaly_score=float(data.get("anomaly_score", 0.0)),
            new_balance=round(float(checking.balance), 2),
        )

    transaction = Transaction(
        user_id=user.id,
        account_id=checking.id,
        ts=_payload_ts(data),
        amount=-amount,
        currency="EUR",
        label=f"{data.get('merchant')} — {data.get('city')}",
        merchant=str(data.get("merchant", "")),
        category=str(data.get("category", "Autres")),
        channel=str(data.get("channel", "en_ligne")),
        city=str(data.get("city", "")),
        country=str(data.get("country", "France")),
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
        device_fingerprint=str(data.get("device_fingerprint", "")),
        ip_address=ip,
        risk_score=float(data.get("risk_score", 0.0)),
        anomaly_score=float(data.get("anomaly_score", 0.0)),
        decision=str(data.get("decision", "accept")),
        status="completed",
        is_fraud=False,
        meta={
            "simulation": True,
            "facteurs": data.get("facteurs", []),
            "explication": data.get("explication", ""),
            "defi": challenge.id,
        },
    )
    db.add(transaction)

    # Débit du compte et mise à jour du profil comportemental
    checking.balance = round(float(checking.balance) - amount, 2)
    db.commit()
    db.refresh(transaction)

    _register_device_if_new(db, user, data)
    refresh_profile(db, user)

    # --- Alerte si l'opération reste très atypique ------------------------
    alert_id = None
    if transaction.anomaly_score >= 0.75:
        alert = alert_for_high_anomaly(
            db,
            user_id=user.id,
            transaction=transaction,
            anomaly_score=transaction.anomaly_score,
            reasons=[str(factor.get("detail", "")) for factor in data.get("facteurs", [])][:3],
        )
        alert_id = alert.id

    log_action(
        db,
        action="paiement_execute",
        user_id=user.id,
        status="ok",
        detail=f"{amount:.2f} € vers {transaction.merchant} (score {transaction.risk_score}/100).",
        ip_address=ip,
    )

    return PaymentResultOut(
        status="completed",
        message=f"Paiement de {amount:.2f} € vers « {transaction.merchant} » validé.",
        transaction_id=transaction.id,
        risk_score=transaction.risk_score,
        anomaly_score=transaction.anomaly_score,
        new_balance=round(float(checking.balance), 2),
        alert_id=alert_id,
    )


# ---------------------------------------------------------------------------
# Fonctions internes
# ---------------------------------------------------------------------------
def _local_to_utc(local_dt):
    """Conversion heure locale (Europe/Paris) -> UTC naïf."""
    from datetime import timezone

    return local_dt.astimezone(timezone.utc).replace(tzinfo=None)


def _payload_ts(data: dict):
    """
    Horodatage de l'opération, fixé par le serveur au moment de l'analyse.

    Le défi conserve l'instant simulé (heure de démonstration) : le débit est
    donc enregistré à cette date, et non à la seconde de validation.
    """
    from datetime import datetime

    raw = data.get("ts")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return now_utc()


def _load_challenge(db: Session, user: User, challenge_id: int) -> PaymentChallenge:
    """Charge un défi d'authentification appartenant à l'utilisateur connecté."""
    challenge = db.scalar(
        select(PaymentChallenge).where(PaymentChallenge.id == challenge_id, PaymentChallenge.user_id == user.id)
    )
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demande de paiement introuvable.")
    return challenge


def _register_device_if_new(db: Session, user: User, data: dict) -> None:
    """
    Enregistre un appareil inconnu comme appareil **non vérifié**.

    Conséquence : les prochains paiements depuis cet appareil ne seront plus
    considérés comme provenant d'un appareil totalement inconnu, mais il restera
    à vérifier (voir le centre de sécurité).
    """
    fingerprint = str(data.get("device_fingerprint") or "")
    if not fingerprint:
        return
    existing = db.scalar(select(Device).where(Device.user_id == user.id, Device.fingerprint == fingerprint))
    if existing is not None:
        existing.last_seen = now_utc()
        db.commit()
        return

    db.add(
        Device(
            user_id=user.id,
            fingerprint=fingerprint,
            label=str(data.get("device_label") or "Appareil non identifié")[:80],
            platform="Inconnu",
            is_trusted=False,
            first_seen=now_utc(),
            last_seen=now_utc(),
        )
    )
    db.commit()

    from app.services.alerts import create_alert

    create_alert(
        db,
        user_id=user.id,
        kind="securite",
        severity="moyen",
        title="Nouvel appareil utilisé",
        message=(
            f"Un paiement a été effectué depuis « {data.get('device_label')} », un appareil non vérifié. "
            "Si ce n'est pas le vôtre, changez votre mot de passe et votre code PIN."
        ),
    )
