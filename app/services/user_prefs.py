"""
Préférences régionales : langues, fuseaux horaires, devises, téléphones.

Toutes les conversions d'affichage passent par ce module afin qu'aucun format
national ne soit codé en dur dans les interfaces :

* montants et pourcentages via **Babel** (séparateurs, position du symbole) ;
* dates via Babel + **zoneinfo** (chaque utilisateur a son fuseau IANA) ;
* téléphones via **phonenumbers** (normalisation au format international E.164) ;
* pays et devises issus des référentiels Babel (ISO 3166-1 / ISO 4217).

Aucune nationalité n'est stockée ni demandée : `country_code` sert uniquement à
l'affichage et à la normalisation du téléphone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import phonenumbers
from babel import Locale
from babel.core import UnknownLocaleError
from babel.dates import format_date as _babel_format_date
from babel.dates import format_datetime as _babel_format_datetime
from babel.dates import get_day_names as _babel_day_names
from babel.numbers import format_currency, get_currency_name, list_currencies

DEFAULT_LOCALE = "fr-FR"
DEFAULT_TIMEZONE = "Europe/Paris"
DEFAULT_CURRENCY = "EUR"
DEFAULT_COUNTRY_CODE = "FR"

# Locales proposées dans le sélecteur : volontairement courtes et extensibles.
COMMON_LOCALES: tuple[str, ...] = (
    "fr-FR",
    "fr-CA",
    "en-US",
    "en-GB",
    "es-ES",
    "de-DE",
    "it-IT",
    "pt-BR",
    "nl-NL",
    "ar-MA",
    "tr-TR",
    "ja-JP",
    "zh-CN",
)

_EXCLUDED_TERRITORIES = {"ZZ", "EU", "QO", "XA", "XB"}


# ---------------------------------------------------------------------------
# Normalisation des valeurs
# ---------------------------------------------------------------------------
def normalize_locale(value: str | None, default: str = DEFAULT_LOCALE) -> str:
    """Valide une locale (« fr-FR », « en_US ») et renvoie sa forme BCP 47."""
    if not value:
        return default
    try:
        parsed = Locale.parse(str(value).strip().replace("-", "_"))
    except (UnknownLocaleError, ValueError, TypeError) as error:
        raise ValueError(f"Locale invalide : {value!r}") from error
    return str(parsed).replace("_", "-")


def normalize_timezone(value: str | None, default: str = DEFAULT_TIMEZONE) -> str:
    """Valide un identifiant IANA (« Europe/Paris », « America/New_York »)."""
    if not value:
        return default
    candidate = str(value).strip()
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
        raise ValueError(f"Fuseau horaire invalide : {value!r}") from error
    return candidate


def normalize_currency(value: str | None, default: str = DEFAULT_CURRENCY) -> str:
    """Valide un code ISO 4217 (« EUR », « usd » -> « USD »)."""
    if not value:
        return default
    candidate = str(value).strip().upper()
    if candidate not in list_currencies():
        raise ValueError(f"Devise ISO 4217 inconnue : {value!r}")
    return candidate


def normalize_country_code(value: str | None, default: str = DEFAULT_COUNTRY_CODE) -> str:
    """Valide un code pays ISO 3166-1 alpha-2 (« FR », « us » -> « US »)."""
    if not value:
        return default
    candidate = str(value).strip().upper()
    territories = _territories(DEFAULT_LOCALE)
    if candidate not in territories:
        raise ValueError(f"Code pays ISO 3166-1 inconnu : {value!r}")
    return candidate


def country_name(code: str, locale: str = DEFAULT_LOCALE) -> str:
    """Nom du pays dans la langue demandée (repli : code brut)."""
    return _territories(locale).get((code or "").upper(), code or "")


def currency_name(code: str, locale: str = DEFAULT_LOCALE) -> str:
    """Nom de la devise dans la langue demandée (repli : code brut)."""
    try:
        return get_currency_name(code, locale=_safe_locale(locale))
    except Exception:  # pragma: no cover - devise exotique sans libellé
        return code


# ---------------------------------------------------------------------------
# Téléphone international
# ---------------------------------------------------------------------------
def normalize_phone(raw: str | None, region: str | None = None) -> str:
    """
    Normalise un numéro au format international E.164 (+33611223344).

    `region` (code pays ISO) permet d'interpréter un numéro local saisi sans
    indicatif. Un numéro invalide lève `ValueError` : la validation appartient
    à la couche qui reçoit la saisie.
    """
    candidate = (raw or "").strip()
    if not candidate:
        return ""
    region_hint = (region or "").strip().upper() or None
    try:
        parsed = phonenumbers.parse(candidate, region_hint)
    except phonenumbers.NumberParseException as error:
        raise ValueError(f"Numéro de téléphone invalide : {raw!r}") from error
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError(f"Numéro de téléphone invalide : {raw!r}")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def mask_phone(phone: str) -> str:
    """Masque un numéro pour l'affichage (« +33•••••••44 »)."""
    raw = (phone or "").strip()
    digits = "".join(character for character in raw if character.isdigit())
    if len(digits) < 4:
        return "numéro non renseigné" if not raw else raw
    prefix = "+" if raw.startswith("+") else ""
    return f"{prefix}{digits[:2]}{'•' * max(0, len(digits) - 4)}{digits[-2:]}"


# ---------------------------------------------------------------------------
# Affichage (montants, pourcentages, dates)
# ---------------------------------------------------------------------------
def format_money(amount: float | None, currency: str = DEFAULT_CURRENCY, locale: str = DEFAULT_LOCALE) -> str:
    """Montant formaté selon la locale (1 234,56 € / €1,234.56)."""
    value = float(amount or 0.0)
    return format_currency(value, currency or DEFAULT_CURRENCY, locale=_safe_locale(locale))


def format_percent(
    value: float | None,
    locale: str = DEFAULT_LOCALE,
    digits: int = 1,
    signed: bool = False,
) -> str:
    """Pourcentage lisible et localisé (22,2 % / 22.2%)."""
    number = round(float(value or 0.0) * 100, digits)
    if digits <= 0:
        text = f"{number:.0f}"
    else:
        text = f"{number:.{digits}f}"
        if locale.lower().startswith(("fr", "es", "pt", "it", "nl", "tr", "de")):
            text = text.replace(".", ",")
    if signed and number > 0:
        text = "+" + text
    separator = " %" if not locale.lower().startswith(("en", "ja", "zh")) else "%"
    return text + separator


def to_timezone(value: datetime | None, timezone_name: str = DEFAULT_TIMEZONE) -> datetime:
    """Convertit un horodatage UTC naïf vers le fuseau de l'utilisateur."""
    tz = ZoneInfo(timezone_name) if timezone_name else ZoneInfo(DEFAULT_TIMEZONE)
    if value is None:
        return datetime.now(timezone.utc).astimezone(tz)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz)


def format_datetime(
    value: datetime | None,
    timezone_name: str = DEFAULT_TIMEZONE,
    locale: str = DEFAULT_LOCALE,
    with_time: bool = True,
) -> str:
    """Date (et heure) localisées dans le fuseau de l'utilisateur."""
    local = to_timezone(value, timezone_name)
    if not with_time:
        return _babel_format_date(local, format="short", locale=_safe_locale(locale))
    return _babel_format_datetime(local, format="short", locale=_safe_locale(locale))


# ---------------------------------------------------------------------------
# Référentiels pour les formulaires
# ---------------------------------------------------------------------------
@lru_cache(maxsize=8)
def _territories(locale: str) -> dict[str, str]:
    """Territoires ISO 3166-1 (code alpha-2 -> nom localisé)."""
    parsed = Locale.parse(_safe_locale_code(locale))
    territories: dict[str, str] = {}
    for code, name in parsed.territories.items():
        if len(code) == 2 and code.isalpha() and code not in _EXCLUDED_TERRITORIES:
            territories[code] = str(name)
    return territories


def country_options(locale: str = DEFAULT_LOCALE) -> list[tuple[str, str]]:
    """Liste (code, nom) des pays, triée par nom localisé."""
    return sorted(_territories(locale).items(), key=lambda item: item[1])


def currency_options(locale: str = DEFAULT_LOCALE) -> list[tuple[str, str]]:
    """Liste (code ISO 4217, nom) des devises."""
    return sorted(
        ((code, currency_name(code, locale)) for code in list_currencies()),
        key=lambda item: item[0],
    )


def timezone_options() -> list[str]:
    """Identifiants IANA disponibles (triés)."""
    return sorted(available_timezones())


def locale_options() -> list[tuple[str, str]]:
    """Locales proposées à l'utilisateur (code, libellé natif)."""
    result: list[tuple[str, str]] = []
    for code in COMMON_LOCALES:
        try:
            display = Locale.parse(code.replace("-", "_")).get_display_name(code.replace("-", "_"))
        except Exception:  # pragma: no cover
            display = code
        result.append((code, f"{display} ({code})" if display else code))
    return result


def weekday_names(locale: str = DEFAULT_LOCALE) -> list[str]:
    """Noms des jours (lundi → dimanche) dans la locale demandée."""
    names = _babel_day_names(width="wide", context="format", locale=_safe_locale(locale))
    return [str(names[index]) for index in range(7)]


def phone_region_for(country_code: str | None) -> str | None:
    """Région à utiliser pour interpréter un numéro local saisi."""
    code = (country_code or "").strip().upper()
    return code if len(code) == 2 else None


# ---------------------------------------------------------------------------
# Interne
# ---------------------------------------------------------------------------
def _safe_locale_code(locale: str) -> str:
    try:
        return str(Locale.parse((locale or DEFAULT_LOCALE).replace("-", "_")))
    except Exception:
        return "fr_FR"


def _safe_locale(locale: str) -> Locale:
    try:
        return Locale.parse((locale or DEFAULT_LOCALE).replace("-", "_"))
    except Exception:
        return Locale.parse("fr_FR")


__all__ = [
    "COMMON_LOCALES",
    "DEFAULT_COUNTRY_CODE",
    "DEFAULT_CURRENCY",
    "DEFAULT_LOCALE",
    "DEFAULT_TIMEZONE",
    "country_name",
    "country_options",
    "currency_name",
    "currency_options",
    "format_datetime",
    "format_money",
    "format_percent",
    "locale_options",
    "mask_phone",
    "normalize_country_code",
    "normalize_currency",
    "normalize_locale",
    "normalize_phone",
    "normalize_timezone",
    "phone_region_for",
    "timezone_options",
    "to_timezone",
    "weekday_names",
]
