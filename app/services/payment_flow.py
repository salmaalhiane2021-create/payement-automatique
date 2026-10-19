"""
Tronc commun du cycle de paiement sécurisé.

Ce module contient la logique métier partagée par les deux interfaces :

* l'API FastAPI (`app/routers/payments.py`) ;
* l'application Streamlit (`streamlit_app.py`).

Il ne dépend d'aucun framework web : il reçoit une session SQLAlchemy et un
dictionnaire décrivant l'opération, puis renvoie des dictionnaires. Les erreurs
métier sont décrites par un code et un message en français ; chaque interface
les traduit ensuite dans son format natif (HTTPException ou message Streamlit).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import now_utc
from app.models import Device, PaymentChallenge, Transaction, User
from app.services import geo
from app.services.alerts import (
    alert_for_blocked_payment,
    alert_for_high_anomaly,
    create_alert,
    log_action,
)
from app.services.behavior import get_profile, refresh_profile, to_local
from app.services.ledger import accounts_of
from app.services.otp import (
    create_challenge,
    masked_phone,
    purge_expired,
    verify_challenge,
)
from app.services.risk_engine import assess_payment


# ---------------------------------------------------------------------------
# Contexte et scénarios de démonstration
# ---------------------------------------------------------------------------
def payment_context(db: Session, user: User) -> dict:
    """Informations nécessaires au formulaire de paiement."""
    from app.seed import MERCHANTS  # catalogue de commerçants (données de démo)

    devices = list(
        db.scalars(
            select(Device).where(Device.user_id == user.id).order_by(Device.last_seen.desc())
        ).all()
    )
    checking, _ = accounts_of(db, user)
    categories = sorted(
        set(MERCHANTS.keys())
        | {"Shopping", "Crypto", "Jeux d'argent", "Transfert international", "Autres"}
    )

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


def demo_scenarios(user: User) -> list[dict]:
    """
    Scénarios prêts à l'emploi pour la démonstration.

    Ce ne sont que des formulaires préremplis : c'est le moteur de risque qui
    décide ensuite. Chaque scénario fige l'heure simulée afin que le résultat
    soit reproductible quel que soit le moment de la démonstration.
    """
    return [
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


# ---------------------------------------------------------------------------
# Normalisation de l'opération
# ---------------------------------------------------------------------------
def simulated_timestamp(hour: int | None, minute: int | None = None) -> datetime:
    """
    Horodatage de l'opération (heure locale simulable pour la démonstration).

    L'heure simulée est ramenée à sa dernière occurrence passée : une
    simulation à 3h du matin correspond ainsi à « cette nuit », ce qui garde
    les analyses de vélocité et d'historique cohérentes.
    """
    ts = now_utc()
    if hour is None:
        return ts
    local = to_local(ts).replace(
        hour=int(hour),
        minute=int(minute or 0),
        second=0,
        microsecond=0,
    )
    if local > to_local(ts):
        local -= timedelta(days=1)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def build_payment(data: dict) -> dict:
    """Normalise les champs d'une demande de paiement en contexte analysable."""
    city = str(data.get("city") or "")
    country = data.get("country") or geo.country_for(city)

    # Coordonnées : celles fournies, sinon celles de la ville connue. Elles sont
    # enregistrées avec l'opération, ce qui permet ensuite de détecter les
    # déplacements impossibles entre deux paiements.
    latitude, longitude = data.get("latitude"), data.get("longitude")
    if latitude is None or longitude is None:
        coords = geo.coords_for(city, country)
        if coords:
            latitude, longitude = coords

    ts = data.get("ts") or simulated_timestamp(
        data.get("simulated_hour"), data.get("simulated_minute")
    )

    return {
        "merchant": str(data.get("merchant") or ""),
        "amount": round(float(data.get("amount") or 0.0), 2),
        "category": str(data.get("category") or "Autres"),
        "city": city,
        "country": str(country or ""),
        "channel": str(data.get("channel") or "en_ligne"),
        "device_fingerprint": str(data.get("device_fingerprint") or ""),
        "device_label": str(data.get("device_label") or "Appareil"),
        "latitude": latitude,
        "longitude": longitude,
        "ts": ts,
        "note": data.get("note"),
    }


# ---------------------------------------------------------------------------
# 1. Analyse de risque et génération du code à usage unique
# ---------------------------------------------------------------------------
def quote(db: Session, user: User, data: dict, ip_address: str = "") -> dict:
    """
    Analyse le risque du paiement puis remet un code à usage unique (60 s).

    Retourne un dictionnaire compatible avec le schéma `PaymentQuoteResponse`.
    """
    purge_expired(db)  # hygiène : les défis expirés ne doivent plus être utilisables

    payment = build_payment(data)
    profile = get_profile(db, user)
    assessment = assess_payment(db, user, payment, profile)
    factors = [dict(factor) for factor in assessment.factors]

    log_action(
        db,
        action="paiement_analyse",
        user_id=user.id,
        status="ok" if assessment.decision != "block" else "bloque",
        detail=(
            f"{payment['amount']:.2f} € vers {payment['merchant']} — score {assessment.score}/100 "
            f"({assessment.decision})"
        ),
        ip_address=ip_address,
    )

    # --- Paiement bloqué : aucune suite possible --------------------------
    if assessment.decision == "block":
        alert_for_blocked_payment(db, user.id, payment, assessment)
        return {
            "decision": "block",
            "decision_label": assessment.decision_label,
            "risk_score": assessment.score,
            "anomaly_score": assessment.anomaly_score,
            "factors": factors,
            "summary": assessment.summary,
            "challenge_id": None,
            "require_pin": False,
            "otp_ttl_seconds": settings.otp_ttl_seconds,
            "simulation_code": None,
            "masked_phone": masked_phone(user.phone),
        }

    # --- Génération du code à usage unique ---------------------------------
    challenge_payload = {
        "purpose": "paiement",
        "ts": payment["ts"].isoformat(),
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
        "facteurs": factors,
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
        detail=(
            f"Défi #{challenge.id} valable {settings.otp_ttl_seconds} s "
            f"(décision : {assessment.decision})."
        ),
        ip_address=ip_address,
    )

    return {
        "decision": assessment.decision,
        "decision_label": assessment.decision_label,
        "risk_score": assessment.score,
        "anomaly_score": assessment.anomaly_score,
        "factors": factors,
        "summary": assessment.summary,
        "challenge_id": challenge.id,
        "require_pin": assessment.require_pin,
        "otp_ttl_seconds": settings.otp_ttl_seconds,
        # En production, ce champ n'existe pas : le code partirait par SMS.
        "simulation_code": None if settings.is_production else code,
        "masked_phone": masked_phone(user.phone),
    }


# ---------------------------------------------------------------------------
# 2. Chargement et annulation d'un défi
# ---------------------------------------------------------------------------
def load_challenge(db: Session, user: User, challenge_id: int) -> PaymentChallenge | None:
    """Charge un défi d'authentification appartenant à l'utilisateur connecté."""
    return db.scalar(
        select(PaymentChallenge).where(
            PaymentChallenge.id == challenge_id,
            PaymentChallenge.user_id == user.id,
        )
    )


def cancel(db: Session, challenge: PaymentChallenge, user_id: int, ip_address: str = "") -> None:
    """Annule un défi en cours (l'utilisateur renonce au paiement)."""
    if challenge.consumed_at is None:
        challenge.consumed_at = now_utc()
        db.commit()
    log_action(
        db,
        "paiement_annule",
        user_id,
        "ok",
        f"Défi #{challenge.id} annulé.",
        ip_address,
    )


# ---------------------------------------------------------------------------
# 3. Validation du code et exécution du paiement
# ---------------------------------------------------------------------------
def confirm(
    db: Session,
    user: User,
    challenge: PaymentChallenge,
    code: str,
    pin: str | None = None,
    ip_address: str = "",
) -> dict:
    """
    Vérifie le code à usage unique puis exécute le paiement simulé.

    Trois formes de résultat :

    * ``{"status": "error", ...}``   : code ou PIN invalide (aucun débit) ;
    * ``{"status": "blocked", ...}`` : solde insuffisant au moment du débit ;
    * ``{"status": "completed", ...}`` : opération exécutée.
    """
    # --- Vérification côté serveur du code (+ PIN si nécessaire) ----------
    valid, error_code, message = verify_challenge(db, challenge, code, pin)
    if not valid:
        log_action(
            db,
            action="paiement_refuse",
            user_id=user.id,
            status="echec",
            detail=f"Défi #{challenge.id} : {error_code} — {message}",
            ip_address=ip_address,
        )
        if error_code in {"trop_dessais", "code_invalide", "pin_invalide"}:
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
        return {"status": "error", "error": error_code, "message": message}

    # --- Exécution de l'opération simulée ---------------------------------
    data = challenge.payload or {}
    amount = round(float(data.get("amount", 0.0)), 2)
    checking, _ = accounts_of(db, user)

    if checking is None:
        return {
            "status": "error",
            "error": "compte_absent",
            "message": "Aucun compte courant associé.",
        }

    # Contrôle de solde au moment de l'exécution (il a pu changer depuis l'analyse)
    if amount > float(checking.balance):
        log_action(
            db,
            "paiement_refuse",
            user.id,
            "bloque",
            "Solde insuffisant au moment du débit.",
            ip_address,
        )
        return {
            "status": "blocked",
            "message": (
                f"Paiement refusé : solde insuffisant ({checking.balance:.2f} € disponibles "
                f"pour {amount:.2f} € demandés)."
            ),
            "risk_score": float(data.get("risk_score", 0.0)),
            "anomaly_score": float(data.get("anomaly_score", 0.0)),
            "new_balance": round(float(checking.balance), 2),
        }

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
        ip_address=ip_address,
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
            reasons=[
                str(factor.get("detail", "")) for factor in data.get("facteurs", [])
            ][:3],
        )
        alert_id = alert.id

    log_action(
        db,
        action="paiement_execute",
        user_id=user.id,
        status="ok",
        detail=(
            f"{amount:.2f} € vers {transaction.merchant} "
            f"(score {transaction.risk_score}/100)."
        ),
        ip_address=ip_address,
    )

    return {
        "status": "completed",
        "message": f"Paiement de {amount:.2f} € vers « {transaction.merchant} » validé.",
        "transaction_id": transaction.id,
        "risk_score": transaction.risk_score,
        "anomaly_score": transaction.anomaly_score,
        "new_balance": round(float(checking.balance), 2),
        "alert_id": alert_id,
    }


# ---------------------------------------------------------------------------
# Fonctions internes
# ---------------------------------------------------------------------------
def _payload_ts(data: dict) -> datetime:
    """
    Horodatage de l'opération, fixé par le serveur au moment de l'analyse.

    Le défi conserve l'instant simulé (heure de démonstration) : le débit est
    donc enregistré à cette date, et non à la seconde de validation.
    """
    raw = data.get("ts")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return now_utc()


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
    existing = db.scalar(
        select(Device).where(Device.user_id == user.id, Device.fingerprint == fingerprint)
    )
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
