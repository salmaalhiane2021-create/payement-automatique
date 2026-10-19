"""
Authentification dynamique : code à usage unique (OTP) de 60 secondes.

Propriétés de sécurité appliquées
---------------------------------
* code aléatoire cryptographique à 6 chiffres, jamais stocké en clair
  (empreinte HMAC-SHA256 liée à l'identifiant du défi) ;
* durée de vie de 60 s (`OTP_TTL_SECONDS`) : passé ce délai, il est inutilisable ;
* **usage unique** : `consumed_at` est renseigné dès la première validation ;
* 3 tentatives maximum (`OTP_MAX_ATTEMPTS`) puis invalidation immédiate ;
* vérification côté serveur uniquement (le code ne circule jamais depuis le
  navigateur vers une logique de confiance) ;
* vérification renforcée : pour une décision « verify », le code PIN est
  exigé en plus du code OTP.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import now_utc
from app.models import PaymentChallenge, User
from app.security import (
    generate_otp_code,
    hash_otp_code,
    verify_otp_code,
    verify_pin,
)

# Codes d'erreur renvoyés au client (traduits dans les messages)
ERROR_EXPIRED = "expire"
ERROR_CONSUMED = "consomme"
ERROR_INVALID = "code_invalide"
ERROR_TOO_MANY = "trop_dessais"
ERROR_PIN = "pin_invalide"
ERROR_NOT_FOUND = "introuvable"


def masked_phone(phone: str) -> str:
    """Masque un numéro de téléphone pour l'affichage (« 06••••••42 »)."""
    digits = "".join(character for character in (phone or "") if character.isdigit())
    if len(digits) < 4:
        return "numéro non renseigné"
    return f"{digits[:2]}{'•' * max(0, len(digits) - 4)}{digits[-2:]}"


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
) -> tuple[bool, str, str]:
    """
    Vérifie un code OTP (et le PIN si nécessaire).

    Retourne (succès, code_erreur, message_en_français). En cas de succès, le
    défi est marqué comme consommé : le même code ne peut pas resservir.
    """
    now = now_utc()

    # L'expiration est vérifiée avant la consommation : un défi purgé par
    # `purge_expired` (marqué consommé à sa date d'expiration) répond donc
    # « code expiré », ce qui est plus juste pour l'utilisateur.
    if now > challenge.expires_at:
        return False, ERROR_EXPIRED, f"Code expiré ({settings.otp_ttl_seconds} s). Relancez le paiement pour un nouveau code."
    if challenge.consumed_at is not None:
        # Un défi invalidé après épuisement des tentatives se distingue d'un
        # code déjà utilisé avec succès : le message doit être sans ambiguïté.
        if int(challenge.attempts or 0) >= settings.otp_max_attempts:
            return False, ERROR_TOO_MANY, "Défi invalidé après trop de tentatives. Relancez le paiement pour un nouveau code."
        return False, ERROR_CONSUMED, "Ce code a déjà été utilisé. Relancez l'opération pour en obtenir un nouveau."
    if challenge.attempts >= settings.otp_max_attempts:
        challenge.consumed_at = now
        db.commit()
        return False, ERROR_TOO_MANY, "Trop de tentatives : ce code est invalidé par sécurité."

    # --- Vérification du code -------------------------------------------
    if not verify_otp_code(challenge.id, (code or "").strip(), challenge.code_hash):
        challenge.attempts += 1
        remaining = max(0, settings.otp_max_attempts - challenge.attempts)
        if remaining == 0:
            challenge.consumed_at = now
        db.commit()
        if remaining == 0:
            return False, ERROR_TOO_MANY, "Code incorrect. Défi invalidé après 3 tentatives."
        return False, ERROR_INVALID, f"Code incorrect. {remaining} tentative(s) restante(s)."

    # --- Second facteur --------------------------------------------------
    if challenge.require_pin:
        if not pin:
            return False, ERROR_PIN, "Code PIN requis pour valider cette opération sensible."
        user = db.get(User, challenge.user_id)
        if not verify_pin(pin, user.pin_hash if user else None):
            # Un PIN erroné consomme une tentative du défi (anti-force brute),
            # sans invalider le code OTP qui, lui, était correct.
            challenge.attempts += 1
            if challenge.attempts >= settings.otp_max_attempts:
                challenge.consumed_at = now
            db.commit()
            remaining = max(0, settings.otp_max_attempts - challenge.attempts)
            if remaining == 0:
                return False, ERROR_TOO_MANY, "Code PIN incorrect. Défi invalidé après 3 tentatives."
            return False, ERROR_PIN, f"Code PIN incorrect. {remaining} tentative(s) restante(s)."

    challenge.consumed_at = now
    db.commit()
    return True, "", "Code validé."


def seconds_remaining(challenge: PaymentChallenge) -> int:
    """Secondes restantes avant expiration (0 si déjà expiré)."""
    remaining = (challenge.expires_at - now_utc()).total_seconds()
    return max(0, int(remaining))


def attempts_left(challenge: PaymentChallenge) -> int:
    """Tentatives restantes sur ce défi."""
    return max(0, settings.otp_max_attempts - int(challenge.attempts or 0))


def purge_expired(db: Session) -> int:
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
    db.commit()
    return len(expired)
