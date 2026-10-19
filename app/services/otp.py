"""
Authentification dynamique : code à usage unique (OTP) de 60 secondes.

Propriétés de sécurité appliquées
---------------------------------
* code aléatoire cryptographique à 6 chiffres, jamais stocké en clair
  (empreinte HMAC-SHA256 liée à l'identifiant du défi) ;
* durée de vie de 60 s (`OTP_TTL_SECONDS`) : passé ce délai, il est inutilisable ;
* **usage unique garanti même en concurrence** : la consommation passe par un
  `UPDATE ... WHERE consumed_at IS NULL` conditionnel (compare-and-set). Si deux
  requêtes valident le même code en parallèle, une seule obtient la ligne ;
  l'autre reçoit « code déjà utilisé » ;
* 3 tentatives maximum (`OTP_MAX_ATTEMPTS`) puis invalidation immédiate ;
* vérification côté serveur uniquement (le code ne circule jamais depuis le
  navigateur vers une logique de confiance) ;
* vérification renforcée : pour une décision « verify », le code PIN est
  exigé en plus du code OTP.

Atomicité : `commit=False` permet à l'appelant (voir `payment_flow.confirm`)
d'inclure la consommation du défi dans la même transaction que le débit et les
alertes. En cas de `rollback`, le défi redevient utilisable : aucune opération
partiellement enregistrée.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import now_utc
from app.i18n import t
from app.models import PaymentChallenge, User
from app.security import (
    generate_otp_code,
    hash_otp_code,
    verify_otp_code,
    verify_pin,
)
from app.services.user_prefs import mask_phone as _mask_phone

# Codes d'erreur renvoyés au client (traduits dans les messages)
ERROR_EXPIRED = "expire"
ERROR_CONSUMED = "consomme"
ERROR_INVALID = "code_invalide"
ERROR_TOO_MANY = "trop_dessais"
ERROR_PIN = "pin_invalide"
ERROR_NOT_FOUND = "introuvable"


def masked_phone(phone: str) -> str:
    """Masque un numéro de téléphone pour l'affichage (« 06••••••44 »)."""
    return _mask_phone(phone)


def create_challenge(
    db: Session,
    user: User,
    payload: dict,
    risk_score: float,
    decision: str,
    require_pin: bool,
) -> tuple[PaymentChallenge, str]:
    """
    Crée un défi d'authentification pour une transaction.

    Retourne (défi, code en clair). Le code en clair n'est utilisé que pour la
    simulation (envoi SMS) : il n'est jamais persisté.
    """
    code = generate_otp_code(6)
    challenge = PaymentChallenge(
        user_id=user.id,
        code_hash="",  # renseigné après l'obtention de l'identifiant
        purpose=payload.get("purpose", "paiement"),
        payload=payload,
        risk_score=float(risk_score),
        decision=decision,
        require_pin=require_pin,
        attempts=0,
        created_at=now_utc(),
        expires_at=now_utc() + timedelta(seconds=settings.otp_ttl_seconds),
    )
    db.add(challenge)
    db.flush()  # attribue challenge.id sans valider la transaction

    # L'empreinte dépend de l'identifiant du défi : impossible de rejouer une
    # empreinte d'un défi sur un autre.
    challenge.code_hash = hash_otp_code(challenge.id, code)
    db.commit()
    db.refresh(challenge)
    return challenge, code


def verify_challenge(
    db: Session,
    challenge: PaymentChallenge,
    code: str,
    pin: str | None = None,
    *,
    commit: bool = True,
    lang: str = "fr",
) -> tuple[bool, str, str]:
    """
    Vérifie un code OTP (et le PIN si nécessaire).

    Retourne (succès, code_erreur, message traduit). En cas de succès, le défi
    est marqué comme consommé **de façon atomique** : le même code ne peut pas
    resservir, même si deux requêtes arrivent en même temps.

    `commit=False` laisse la transaction ouverte pour que l'appelant inclue la
    consommation dans une transaction plus large (paiement + alertes + audit).
    """
    now = now_utc()

    # L'expiration est vérifiée avant la consommation : un défi purgé par
    # `purge_expired` (marqué consommé à sa date d'expiration) répond donc
    # « code expiré », ce qui est plus juste pour l'utilisateur.
    if now > challenge.expires_at:
        return False, ERROR_EXPIRED, t("otp.expired", lang, ttl=settings.otp_ttl_seconds)
    if challenge.consumed_at is not None:
        # Un défi invalidé après épuisement des tentatives se distingue d'un
        # code déjà utilisé avec succès : le message doit être sans ambiguïté.
        if int(challenge.attempts or 0) >= settings.otp_max_attempts:
            return False, ERROR_TOO_MANY, t("otp.too_many", lang)
        return False, ERROR_CONSUMED, t("otp.consumed", lang)
    if challenge.attempts >= settings.otp_max_attempts:
        _consume(db, challenge, now)
        if commit:
            db.commit()
        else:
            db.flush()
        return False, ERROR_TOO_MANY, t("otp.invalidated", lang)

    # --- Vérification du code -------------------------------------------
    if not verify_otp_code(challenge.id, (code or "").strip(), challenge.code_hash):
        remaining = _register_failed_attempt(db, challenge, commit=commit)
        if remaining == 0:
            return False, ERROR_TOO_MANY, t("otp.incorrect_final", lang, max=settings.otp_max_attempts)
        return False, ERROR_INVALID, t("otp.incorrect", lang, remaining=remaining)

    # --- Second facteur --------------------------------------------------
    if challenge.require_pin:
        if not pin:
            return False, ERROR_PIN, t("otp.pin_required", lang)
        user = db.get(User, challenge.user_id)
        if not verify_pin(pin, user.pin_hash if user else None):
            # Un PIN erroné consomme une tentative du défi (anti-force brute),
            # sans invalider le code OTP qui, lui, était correct.
            remaining = _register_failed_attempt(db, challenge, commit=commit)
            if remaining == 0:
                return False, ERROR_TOO_MANY, t("otp.pin_final", lang, max=settings.otp_max_attempts)
            return False, ERROR_PIN, t("otp.pin_incorrect", lang, remaining=remaining)

    # --- Consommation atomique (usage unique) ----------------------------
    if not _consume(db, challenge, now):
        # Une autre requête a consommé le défi entre-temps : on refuse sans
        # exécuter le paiement une seconde fois.
        if commit:
            db.rollback()
        return False, ERROR_CONSUMED, t("otp.consumed", lang)

    if commit:
        db.commit()
    else:
        db.flush()
    return True, "", t("otp.validated", lang)


def seconds_remaining(challenge: PaymentChallenge) -> int:
    """Secondes restantes avant expiration (0 si déjà expiré)."""
    remaining = (challenge.expires_at - now_utc()).total_seconds()
    return max(0, int(remaining))


def attempts_left(challenge: PaymentChallenge) -> int:
    """Tentatives restantes sur ce défi."""
    return max(0, settings.otp_max_attempts - int(challenge.attempts or 0))


def purge_expired(db: Session, commit: bool = True) -> int:
    """
    Supprime les défis expirés non consommés (hygiène de la base).

    Les défis consommés sont conservés : ils constituent une piste d'audit.
    """
    expired = db.scalars(
        select(PaymentChallenge).where(
            PaymentChallenge.expires_at < now_utc(),
            PaymentChallenge.consumed_at.is_(None),
        )
    ).all()
    for challenge in expired:
        # On les marque consommés plutôt que de les supprimer (traçabilité).
        challenge.consumed_at = challenge.expires_at
    db.flush()
    if commit:
        db.commit()
    return len(expired)


# ---------------------------------------------------------------------------
# Opérations atomiques internes (compare-and-set)
# ---------------------------------------------------------------------------
def _consume(db: Session, challenge: PaymentChallenge, now) -> bool:
    """
    Marque le défi consommé si et seulement s'il ne l'est pas déjà.

    Retourne False si une autre transaction l'a consommé en premier : c'est ce
    verrou conditionnel qui garantit l'usage unique en concurrence.
    """
    result = db.execute(
        update(PaymentChallenge)
        .where(PaymentChallenge.id == challenge.id, PaymentChallenge.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    if result.rowcount != 1:
        db.refresh(challenge)
        return False
    challenge.consumed_at = now
    return True


def _register_failed_attempt(db: Session, challenge: PaymentChallenge, *, commit: bool) -> int:
    """
    Incrémente le compteur d'essais de façon atomique et renvoie le solde.

    Le compteur est incrémenté en SQL (`attempts = attempts + 1`) : deux
    tentatives simultanées ne peuvent pas s'écraser mutuellement. À la dernière
    tentative, le défi est invalidé.
    """
    result = db.execute(
        update(PaymentChallenge)
        .where(PaymentChallenge.id == challenge.id, PaymentChallenge.consumed_at.is_(None))
        .values(attempts=PaymentChallenge.attempts + 1)
    )
    if result.rowcount == 1:
        db.flush()
        db.refresh(challenge)
    else:
        db.refresh(challenge)

    attempts = int(challenge.attempts or 0)
    if attempts >= settings.otp_max_attempts and challenge.consumed_at is None:
        _consume(db, challenge, now_utc())
    if commit:
        db.commit()
    else:
        db.flush()
    return max(0, settings.otp_max_attempts - attempts)


__all__ = [
    "ERROR_CONSUMED",
    "ERROR_EXPIRED",
    "ERROR_INVALID",
    "ERROR_NOT_FOUND",
    "ERROR_PIN",
    "ERROR_TOO_MANY",
    "attempts_left",
    "create_challenge",
    "masked_phone",
    "purge_expired",
    "seconds_remaining",
    "verify_challenge",
]
