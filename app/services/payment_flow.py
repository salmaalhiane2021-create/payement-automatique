"""
Tronc commun du cycle de paiement sécurisé.

Ce module contient la logique métier partagée par les deux interfaces :

* l'API FastAPI (`app/routers/payments.py`) ;
* l'application Streamlit (`streamlit_ui/`).

Il ne dépend d'aucun framework web : il reçoit une session SQLAlchemy et un
dictionnaire décrivant l'opération, puis renvoie des dictionnaires localisés
(``lang`` = « fr » ou « en »). Chaque interface traduit ensuite le résultat
dans son format natif (HTTPException ou message Streamlit).

Atomicité de la confirmation
----------------------------
`confirm()` exécute **une seule transaction** couvrant :

1. la consommation atomique du défi OTP (`UPDATE ... WHERE consumed_at IS NULL` :
   un code ne peut jamais être utilisé deux fois, même en concurrence) ;
2. le débit du compte (mise à jour conditionnelle `balance >= montant`) ;
3. l'enregistrement de la transaction, de l'appareil et du profil ;
4. les alertes et le journal d'audit.

En cas d'exception, un `rollback` annule l'ensemble : aucune opération
partiellement enregistrée. Le défi redevient utilisable si rien n'a été validé.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import now_utc
from app.i18n import t
from app.models import Account, Device, PaymentChallenge, Transaction, User
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


def _amount_text(value: float) -> str:
    """Montant technique « 620.00 € » (messages d'API, parité historique)."""
    return f"{float(value):.2f} €"


def _factor_label(factor: dict, lang: str) -> str:
    """Libellé traduit d'un facteur de risque (repli : libellé enregistré)."""
    code = str(factor.get("code") or "")
    if code.startswith("step_up_"):
        return t("factor.step_up", lang)
    translated = t(f"factor.{code}", lang)
    if translated.startswith("factor."):
        return str(factor.get("label") or code)
    return translated


def localize_factors(factors: list[dict], lang: str = "fr") -> list[dict]:
    """Copie des facteurs avec libellé traduit + clé i18n stable."""
    localized: list[dict] = []
    for factor in factors or []:
        item = dict(factor)
        item["label_key"] = f"factor.{factor.get('code', '')}"
        item["label"] = _factor_label(factor, lang)
        localized.append(item)
    return localized


def _localized_summary(assessment, payment: dict, lang: str) -> str:
    """
    Phrase de synthèse localisée.

    En français, la phrase rédigée par le moteur de risque est conservée telle
    quelle (parité avec l'API historique). En anglais, la synthèse est
    reconstruite à partir de la décision et des libellés traduits.
    """
    if lang == "fr" or not lang:
        return assessment.summary

    amount = float(payment.get("amount") or 0.0)
    top = [_factor_label(factor, lang) for factor in assessment.factors if factor["weight"] >= 5][:3]
    reasons = ", ".join(top) or t("risk.reasons_default", lang)

    if assessment.decision == "block":
        if assessment.hard_block:
            reason = next(
                (factor["detail"] for factor in assessment.factors if factor["severity"] == "critique"),
                t("risk.reasons_default", lang),
            )
            return t("risk.summary.block_hard", lang, reason=reason)
        return t(
            "risk.summary.block",
            lang,
            amount=_amount_text(amount),
            score=f"{assessment.score:.0f}",
            reasons=reasons,
        )
    if assessment.decision == "verify":
        return t("risk.summary.verify", lang, score=f"{assessment.score:.0f}", reasons=reasons)
    return t("risk.summary.accept", lang, score=f"{assessment.score:.0f}")


# ---------------------------------------------------------------------------
# Contexte et scénarios de démonstration
# ---------------------------------------------------------------------------
def _demo_city(user: User) -> str:
    """Ville utilisée par les scénarios : celle de l'utilisateur si connue."""
    return user.city if user.city in geo.CITY_COORDS else "Paris"


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
    soit reproductible quel que soit le moment de la démonstration. La ville de
    l'utilisateur est utilisée lorsqu'elle est connue du référentiel : les
    scénarios ne dépendent donc pas d'un pays en particulier.
    """
    home_city = _demo_city(user)
    home_country = geo.country_for(home_city) or ""

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
                "city": home_city,
                "country": home_country,
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
                "city": home_city,
                "country": home_country,
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
                "city": home_city,
                "country": home_country,
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
                "city": home_city,
                "country": home_country,
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
                "city": home_city,
                "country": home_country,
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
def simulated_timestamp(
    hour: int | None,
    minute: int | None = None,
    timezone_name: str | None = None,
) -> datetime:
    """
    Horodatage de l'opération (heure locale simulable pour la démonstration).

    L'heure simulée est interprétée dans le fuseau de l'utilisateur puis ramenée
    à sa dernière occurrence passée : une simulation à 3h du matin correspond à
    « cette nuit » dans son pays, ce qui garde les analyses de vélocité et
    d'historique cohérentes.
    """
    ts = now_utc()
    if hour is None:
        return ts
    local = to_local(ts, timezone_name).replace(
        hour=int(hour),
        minute=int(minute or 0),
        second=0,
        microsecond=0,
    )
    if local > to_local(ts, timezone_name):
        local -= timedelta(days=1)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def build_payment(data: dict, timezone_name: str | None = None) -> dict:
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
        data.get("simulated_hour"),
        data.get("simulated_minute"),
        timezone_name,
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
def quote(db: Session, user: User, data: dict, ip_address: str = "", lang: str = "fr") -> dict:
    """
    Analyse le risque du paiement puis remet un code à usage unique (60 s).

    Retourne un dictionnaire compatible avec le schéma `PaymentQuoteResponse`.
    """
    purge_expired(db)  # hygiène : les défis expirés ne doivent plus être utilisables

    payment = build_payment(data, user.timezone)
    profile = get_profile(db, user)
    assessment = assess_payment(db, user, payment, profile)
    factors = localize_factors([dict(factor) for factor in assessment.factors], lang)

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
        alert_for_blocked_payment(db, user.id, payment, assessment, lang=lang)
        return {
            "decision": "block",
            "decision_label": t("decision.block", lang),
            "risk_score": assessment.score,
            "anomaly_score": assessment.anomaly_score,
            "factors": factors,
            "summary": _localized_summary(assessment, payment, lang),
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
        "explication": _localized_summary(assessment, payment, lang),
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
        "decision_label": t(f"decision.{assessment.decision}", lang),
        "risk_score": assessment.score,
        "anomaly_score": assessment.anomaly_score,
        "factors": factors,
        "summary": _localized_summary(assessment, payment, lang),
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
    lang: str = "fr",
) -> dict:
    """
    Vérifie le code à usage unique puis exécute le paiement simulé.

    Trois formes de résultat :

    * ``{"status": "error", ...}``   : code ou PIN invalide (aucun débit) ;
    * ``{"status": "blocked", ...}`` : solde insuffisant au moment du débit ;
    * ``{"status": "completed", ...}`` : opération exécutée.

    La consommation du code, le débit, la transaction, l'appareil, le profil,
    les alertes et l'audit sont validés par **un seul `commit`**. Toute
    exception déclenche un `rollback` complet.
    """
    # --- Vérification côté serveur du code (+ PIN si nécessaire) ----------
    # `commit=False` : la consommation du défi et les compteurs d'essais
    # restent dans la transaction courante, avec le débit et les alertes.
    valid, error_code, message = verify_challenge(
        db, challenge, code, pin, commit=False, lang=lang
    )
    if not valid:
        try:
            log_action(
                db,
                action="paiement_refuse",
                user_id=user.id,
                status="echec",
                detail=f"Défi #{challenge.id} : {error_code} — {message}",
                ip_address=ip_address,
                commit=False,
            )
            if error_code in {"trop_dessais", "code_invalide", "pin_invalide"}:
                create_alert(
                    db,
                    user_id=user.id,
                    kind="securite",
                    severity="eleve" if error_code == "trop_dessais" else "moyen",
                    title=t("alert.failed_validation.title", lang),
                    message=t(
                        "alert.failed_validation.message",
                        lang,
                        merchant=(challenge.payload or {}).get("merchant"),
                        message=message,
                    ),
                    details={"defi": challenge.id, "erreur": error_code},
                    commit=False,
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"status": "error", "error": error_code, "message": message}

    # --- Exécution de l'opération simulée ---------------------------------
    data = challenge.payload or {}
    amount = round(float(data.get("amount", 0.0)), 2)
    checking, _ = accounts_of(db, user)

    if checking is None:
        # Rien n'a été débité : on annule la consommation du défi.
        db.rollback()
        log_action(
            db,
            "paiement_refuse",
            user.id,
            "echec",
            "Aucun compte courant associé.",
            ip_address,
        )
        return {
            "status": "error",
            "error": "compte_absent",
            "message": t("payment.account_missing", lang),
        }

    # Contrôle de solde au moment de l'exécution (il a pu changer depuis l'analyse)
    available = float(checking.balance)
    if amount > available:
        return _blocked_for_funds(db, user, challenge, amount, available, ip_address, lang)

    # --- Exécution atomique de l'opération simulée ------------------------
    # Toute exception annule l'ensemble : défi, débit, transaction, appareil,
    # profil, alertes et audit (aucune écriture partielle).
    try:
        # Débit conditionnel : si le solde a changé entre la lecture et
        # l'écriture, la condition `balance >= montant` échoue et le paiement
        # est refusé (protection contre les débits concurrents).
        debit = db.execute(
            update(Account)
            .where(Account.id == checking.id, Account.balance >= amount)
            .values(balance=Account.balance - amount)
        )
        if debit.rowcount != 1:
            refreshed = float(
                db.scalar(select(Account.balance).where(Account.id == checking.id)) or 0.0
            )
            # Le défi reste consommé et l'événement journalisé.
            return _blocked_for_funds(db, user, challenge, amount, refreshed, ip_address, lang)

        db.flush()
        new_balance = round(
            float(db.scalar(select(Account.balance).where(Account.id == checking.id)) or 0.0), 2
        )
        checking.balance = new_balance

        transaction = Transaction(
            user_id=user.id,
            account_id=checking.id,
            ts=_payload_ts(data),
            amount=-amount,
            currency="EUR",  # devise de base du prototype (aucune conversion inventée)
            label=f"{data.get('merchant')} — {data.get('city')}",
            merchant=str(data.get("merchant", "")),
            category=str(data.get("category", "Autres")),
            channel=str(data.get("channel", "en_ligne")),
            city=str(data.get("city", "")),
            country=str(data.get("country", "")),
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
        db.flush()  # identifiant de la transaction pour l'alerte éventuelle

        alert = None
        _register_device_if_new(db, user, data, lang=lang)
        refresh_profile(db, user, commit=False)

        # --- Alerte si l'opération reste très atypique --------------------
        if transaction.anomaly_score >= 0.75:
            alert = alert_for_high_anomaly(
                db,
                user_id=user.id,
                transaction=transaction,
                anomaly_score=transaction.anomaly_score,
                reasons=[
                    str(factor.get("detail", "")) for factor in data.get("facteurs", [])
                ][:3],
                commit=False,
                lang=lang,
            )

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
            commit=False,
        )
        # --- Validation atomique de toute l'opération ---------------------
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "status": "completed",
        "message": t(
            "payment.completed",
            lang,
            amount=_amount_text(amount),
            merchant=transaction.merchant,
        ),
        "transaction_id": transaction.id,
        "risk_score": transaction.risk_score,
        "anomaly_score": transaction.anomaly_score,
        "new_balance": new_balance,
        "alert_id": alert.id if alert is not None else None,
    }


# ---------------------------------------------------------------------------
# Fonctions internes
# ---------------------------------------------------------------------------
def _blocked_for_funds(
    db: Session,
    user: User,
    challenge: PaymentChallenge,
    amount: float,
    available: float,
    ip_address: str,
    lang: str,
) -> dict:
    """
    Refus pour solde insuffisant : le défi reste consommé et l'événement est
    journalisé (comportement historique), mais aucun débit n'est enregistré.
    """
    log_action(
        db,
        "paiement_refuse",
        user.id,
        "bloque",
        "Solde insuffisant au moment du débit.",
        ip_address,
        commit=False,
    )
    db.commit()
    return {
        "status": "blocked",
        "message": t(
            "payment.insufficient_funds",
            lang,
            available=_amount_text(available),
            amount=_amount_text(amount),
        ),
        "risk_score": float((challenge.payload or {}).get("risk_score", 0.0)),
        "anomaly_score": float((challenge.payload or {}).get("anomaly_score", 0.0)),
        "new_balance": round(available, 2),
    }


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


def _register_device_if_new(db: Session, user: User, data: dict, lang: str = "fr") -> None:
    """
    Enregistre un appareil inconnu comme appareil **non vérifié**.

    Conséquence : les prochains paiements depuis cet appareil ne seront plus
    considérés comme provenant d'un appareil totalement inconnu, mais il restera
    à vérifier (voir le centre de sécurité). Ne commite pas : appelé dans la
    transaction du paiement.
    """
    fingerprint = str(data.get("device_fingerprint") or "")
    if not fingerprint:
        return
    existing = db.scalar(
        select(Device).where(Device.user_id == user.id, Device.fingerprint == fingerprint)
    )
    if existing is not None:
        existing.last_seen = now_utc()
        db.flush()
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
    db.flush()

    create_alert(
        db,
        user_id=user.id,
        kind="securite",
        severity="moyen",
        title=t("alert.new_device.title", lang),
        message=t(
            "alert.new_device.message",
            lang,
            device=data.get("device_label") or "Appareil non identifié",
        ),
        commit=False,
    )


__all__ = [
    "build_payment",
    "cancel",
    "confirm",
    "demo_scenarios",
    "load_challenge",
    "localize_factors",
    "payment_context",
    "quote",
    "simulated_timestamp",
]
