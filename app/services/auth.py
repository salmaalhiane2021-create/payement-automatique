"""
Service d'authentification partagé (API FastAPI **et** interface Streamlit).

Règle d'architecture : une seule implémentation des règles métier
(anti-énumération, verrouillage après échecs, `is_active`, préférences
régionales). Les interfaces ne font que traduire le résultat en réponse HTTP
ou en message d'interface — voir `app/routers/auth.py` et `streamlit_ui/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import now_utc
from app.i18n import t
from app.models import Account, Device, User
from app.security import (
    generate_device_fingerprint,
    hash_password,
    hash_pin,
    validate_password_strength,
    validate_pin_format,
    verify_password,
)
from app.services import user_prefs
from app.services.alerts import log_action

AuthStatus = Literal["ok", "locked", "invalid", "inactive"]


@dataclass
class RegistrationResult:
    """Résultat de la création d'un compte (succès ou erreur typée)."""

    user: User | None = None
    error_code: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.user is not None


@dataclass
class AuthResult:
    """Résultat d'une tentative de connexion, prêt à être affiché."""

    status: AuthStatus
    user: User | None = None
    message: str = ""
    remaining_minutes: int = 0
    attempts_left: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def normalize_email(email: str) -> str:
    """Adresse normalisée : espaces retirés, minuscules (anti-doublons)."""
    return (email or "").strip().lower()


# ---------------------------------------------------------------------------
# Connexion
# ---------------------------------------------------------------------------
def authenticate(
    db: Session,
    email: str,
    password: str,
    *,
    ip_address: str = "",
    user_agent: str = "",
    lang: str = "fr",
) -> AuthResult:
    """
    Authentifie un utilisateur.

    Le message d'erreur est identique que l'adresse existe ou non
    (anti-énumération). Les tentatives sont journalisées ; après
    `LOGIN_MAX_ATTEMPTS` échecs, le compte est verrouillé temporairement.
    """
    normalized = normalize_email(email)
    user = db.scalar(select(User).where(User.email == normalized))

    # --- Verrouillage temporaire en cours --------------------------------
    if user and user.locked_until and user.locked_until > now_utc():
        remaining = int((user.locked_until - now_utc()).total_seconds() // 60) + 1
        log_action(db, "connexion", user.id, "bloque", f"Compte verrouillé ({remaining} min).", ip_address, user_agent)
        return AuthResult(
            status="locked",
            user=user,
            message=t("auth.locked", lang, minutes=remaining),
            remaining_minutes=remaining,
        )

    # --- Mot de passe incorrect ou compte inconnu ------------------------
    if user is None or not verify_password(password, user.password_hash):
        if user is not None:
            user.failed_attempts = (user.failed_attempts or 0) + 1
            if user.failed_attempts >= settings.login_max_attempts:
                user.locked_until = now_utc() + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_attempts = 0
                attempts_left = 0
                detail = f"Compte verrouillé pour {settings.login_lockout_minutes} minutes."
            else:
                attempts_left = max(0, settings.login_max_attempts - user.failed_attempts)
                detail = f"Échec de connexion ({user.failed_attempts}/{settings.login_max_attempts})."
            db.commit()
            log_action(db, "connexion", user.id, "echec", detail, ip_address, user_agent)
        else:
            log_action(db, "connexion", None, "echec", f"Adresse inconnue : {normalized}", ip_address, user_agent)
            attempts_left = None
        return AuthResult(
            status="invalid",
            message=t("auth.invalid_credentials", lang),
            attempts_left=attempts_left,
        )

    # --- Compte désactivé -------------------------------------------------
    if not user.is_active:
        return AuthResult(status="inactive", user=user, message=t("auth.inactive", lang))

    # --- Connexion réussie ------------------------------------------------
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now_utc()
    db.commit()
    log_action(db, "connexion", user.id, "ok", "Connexion réussie.", ip_address, user_agent)
    return AuthResult(status="ok", user=user)


# ---------------------------------------------------------------------------
# Création de compte
# ---------------------------------------------------------------------------
def create_user_account(
    db: Session,
    *,
    full_name: str,
    email: str,
    password: str,
    pin: str,
    phone: str = "",
    city: str = "",
    country: str = "",
    country_code: str = "",
    locale: str = "",
    timezone: str = "",
    currency: str = "",
    ip_address: str = "",
    lang: str = "fr",
) -> RegistrationResult:
    """
    Crée un compte et ses comptes bancaires simulés.

    Retourne un `RegistrationResult` : soit l'utilisateur créé, soit un code
    d'erreur (« conflict », « password », « pin », « phone », « currency »,
    « timezone », « locale ») et un message déjà traduit.
    """
    normalized = normalize_email(email)

    valid, message = validate_password_strength(password)
    if not valid:
        return RegistrationResult(error_code="password", message=message)
    if not validate_pin_format(pin):
        return RegistrationResult(error_code="pin", message=t("auth.register.pin_invalid", lang))
    if db.scalar(select(User).where(User.email == normalized)):
        return RegistrationResult(error_code="conflict", message=t("auth.register.conflict", lang))

    # --- Préférences régionales (validation explicite) --------------------
    try:
        resolved_country_code = user_prefs.normalize_country_code(country_code or None)
    except ValueError:
        return RegistrationResult(error_code="country", message=t("auth.register.locale_invalid", lang))
    try:
        resolved_locale = user_prefs.normalize_locale(locale or None)
    except ValueError:
        return RegistrationResult(error_code="locale", message=t("auth.register.locale_invalid", lang))
    try:
        resolved_timezone = user_prefs.normalize_timezone(timezone or None)
    except ValueError:
        return RegistrationResult(error_code="timezone", message=t("auth.register.timezone_invalid", lang))
    try:
        resolved_currency = user_prefs.normalize_currency(currency or None)
    except ValueError:
        return RegistrationResult(error_code="currency", message=t("auth.register.currency_invalid", lang))

    try:
        resolved_phone = user_prefs.normalize_phone(
            phone, region=user_prefs.phone_region_for(resolved_country_code)
        )
    except ValueError:
        return RegistrationResult(error_code="phone", message=t("auth.register.phone_invalid", lang))

    resolved_country = (country or "").strip() or user_prefs.country_name(
        resolved_country_code, resolved_locale
    )

    user = User(
        full_name=full_name.strip(),
        email=normalized,
        password_hash=hash_password(password),
        pin_hash=hash_pin(pin),
        phone=resolved_phone,
        city=city.strip(),
        country=resolved_country,
        country_code=resolved_country_code,
        locale=resolved_locale,
        timezone=resolved_timezone,
        # La devise d'affichage est une préférence ; les comptes simulés restent
        # libellés en EUR (devise de base), sans conversion inventée.
        currency=resolved_currency,
        monthly_income=2500.0,
        created_at=now_utc(),
    )
    db.add(user)
    db.flush()

    # Comptes simulés : compte courant de démonstration + épargne vide.
    db.add_all(
        [
            Account(
                user_id=user.id,
                kind="courant",
                label="Compte courant",
                iban_masked=f"{resolved_country_code}00 •••• •••• {user.id:04d}",
                balance=1800.0,
                currency="EUR",
            ),
            Account(
                user_id=user.id,
                kind="epargne",
                label="Livret d'épargne",
                iban_masked=f"{resolved_country_code}01 •••• •••• {user.id:04d}",
                balance=0.0,
                currency="EUR",
            ),
        ]
    )
    db.add(
        Device(
            user_id=user.id,
            fingerprint=generate_device_fingerprint(user.id, "appareil-inscription"),
            label="Appareil d'inscription",
            platform="Navigateur",
            is_trusted=True,
        )
    )
    db.commit()
    log_action(db, action="inscription", user_id=user.id, detail="Nouveau compte créé.", ip_address=ip_address)
    return RegistrationResult(user=user)


# ---------------------------------------------------------------------------
# Mise à jour des préférences régionales
# ---------------------------------------------------------------------------
def update_user_profile(
    db: Session,
    user: User,
    *,
    city: str | None = None,
    country: str | None = None,
    country_code: str | None = None,
    phone: str | None = None,
    locale: str | None = None,
    timezone: str | None = None,
    currency: str | None = None,
    ip_address: str = "",
    lang: str = "fr",
) -> tuple[bool, str]:
    """
    Met à jour les coordonnées et préférences régionales (champs fournis seulement).

    Retourne ``(succès, message)``. Un numéro local saisi sans indicatif est
    interprété avec le pays enregistré (ou le nouveau pays fourni).
    """
    try:
        if country_code is not None:
            user.country_code = user_prefs.normalize_country_code(country_code)
        if locale is not None:
            user.locale = user_prefs.normalize_locale(locale)
        if timezone is not None:
            user.timezone = user_prefs.normalize_timezone(timezone)
        if currency is not None:
            user.currency = user_prefs.normalize_currency(currency)
        if country is not None:
            user.country = country.strip() or user_prefs.country_name(user.country_code, user.locale)
        if city is not None:
            user.city = city.strip()
        if phone is not None:
            user.phone = user_prefs.normalize_phone(
                phone, region=user_prefs.phone_region_for(user.country_code)
            )
    except ValueError as error:
        text = str(error)
        if "Devise" in text:
            return False, t("auth.register.currency_invalid", lang)
        if "Fuseau" in text:
            return False, t("auth.register.timezone_invalid", lang)
        if "téléphone" in text.lower():
            return False, t("auth.register.phone_invalid", lang)
        return False, t("auth.register.locale_invalid", lang)

    db.commit()
    log_action(db, "profil_maj", user.id, "ok", "Préférences régionales mises à jour.", ip_address)
    return True, t("auth.profile.updated", lang)


__all__ = [
    "AuthResult",
    "RegistrationResult",
    "authenticate",
    "create_user_account",
    "normalize_email",
    "update_user_profile",
]
