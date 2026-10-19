"""
Primitives de sécurité : hachage des mots de passe / PIN, jetons JWT,
empreintes de codes OTP et politique de mot de passe.

Choix techniques
----------------
* Mots de passe et PIN : **bcrypt** (coût configurable, sel aléatoire inclus),
  jamais stockés en clair.
* Codes OTP : **HMAC-SHA256** avec la clé secrète de l'application. Le code à
  6 chiffres est trop court pour un hachage lent (l'espace est de 10^6) : la
  protection vient surtout de la durée de vie (60 s), du nombre d'essais
  limité (3) et du caractère à usage unique.
* Sessions : **JWT HS256** signés, contenant l'identifiant utilisateur.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

# bcrypt tronque au-delà de 72 octets : on limite explicitement pour éviter
# les comportements surprenants (et l'erreur ValueError des versions récentes).
_BCRYPT_MAX_BYTES = 72


# ---------------------------------------------------------------------------
# Mots de passe / PIN
# ---------------------------------------------------------------------------
def _prepare_secret(value: str) -> bytes:
    """Encode et tronque un secret à la taille maximale acceptée par bcrypt."""
    return value.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Retourne l'empreinte bcrypt (coût 12) d'un mot de passe."""
    return bcrypt.hashpw(_prepare_secret(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Vérifie un mot de passe contre son empreinte (comparaison sûre)."""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(_prepare_secret(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_pin(pin: str) -> str:
    """Hachage du code PIN (4 à 6 chiffres)."""
    return hash_password(f"pin:{pin}")


def verify_pin(pin: str, pin_hash: str | None) -> bool:
    """Vérifie un code PIN."""
    return verify_password(f"pin:{pin}", pin_hash)


# ---------------------------------------------------------------------------
# Politique de mot de passe
# ---------------------------------------------------------------------------
_PASSWORD_RE = re.compile(r"^[\x20-\x7E]+$")  # caractères ASCII imprimables


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Contrôle la robustesse d'un mot de passe.

    Retourne (valide, message_en_français).
    """
    minimum = settings.password_min_length
    if len(password) < minimum:
        return False, f"Le mot de passe doit contenir au moins {minimum} caractères."
    if not _PASSWORD_RE.match(password):
        return False, "Le mot de passe ne doit contenir que des caractères simples (sans accent ni emoji)."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return False, "Le mot de passe doit contenir au moins une lettre et un chiffre."
    if password.lower() in {"password", "motdepasse", "12345678", "azerty123"}:
        return False, "Mot de passe trop courant, choisissez-en un autre."
    return True, ""


def validate_pin_format(pin: str) -> bool:
    """Le PIN doit être composé de 4 à 6 chiffres."""
    return bool(re.fullmatch(r"\d{4,6}", pin or ""))


# ---------------------------------------------------------------------------
# Jetons de session (JWT)
# ---------------------------------------------------------------------------
def create_access_token(user_id: int, email: str, extra: dict | None = None) -> tuple[str, int]:
    """
    Crée un jeton d'accès signé.

    Retourne (jeton, durée_de_vie_en_secondes).
    """
    expires_in = settings.access_token_expire_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "iss": settings.app_name,
        **(extra or {}),
    }
    token = jwt.encode(payload, settings.resolved_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> dict | None:
    """Décode et vérifie un jeton ; retourne None s'il est invalide ou expiré."""
    try:
        return jwt.decode(
            token,
            settings.resolved_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.app_name,
        )
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# Codes OTP (authentification dynamique)
# ---------------------------------------------------------------------------
def generate_otp_code(length: int = 6) -> str:
    """Génère un code numérique cryptographiquement sûr (6 chiffres)."""
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def hash_otp_code(challenge_id: int | str, code: str) -> str:
    """
    Empreinte d'un code OTP, liée à l'identifiant du défi.

    Lier le code au défi empêche de réutiliser une empreinte d'un défi à
    l'autre (protection contre le rejeu croisé).
    """
    message = f"{challenge_id}:{code}".encode("utf-8")
    key = settings.resolved_secret_key.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_otp_code(challenge_id: int | str, code: str, code_hash: str) -> bool:
    """Comparaison à temps constant de l'empreinte du code."""
    return hmac.compare_digest(hash_otp_code(challenge_id, code), code_hash)


def generate_device_fingerprint(user_id: int, label: str = "") -> str:
    """Empreinte d'appareil (identifiant de confiance) pour la démo."""
    seed = f"{user_id}:{label}:{secrets.token_hex(8)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
