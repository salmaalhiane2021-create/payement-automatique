"""
Mise en forme localisée des valeurs affichées.

Tout passe par `app/services/user_prefs.py` (Babel + zoneinfo) : aucun format
national n'est codé en dur. Les montants restent libellés en EUR (devise de
base) ; une conversion n'a lieu que si une source de taux est configurée
(`FX_RATES_JSON`), sinon l'affichage l'indique explicitement.
"""

from __future__ import annotations

from datetime import datetime

from app.i18n import t
from app.services import fx, user_prefs

from streamlit_ui.context import AppContext

#: Libellés « courts » de décision utilisés dans les badges et scénarios.
DECISION_CODES = {"accept", "verify", "block"}
STATUS_CODES = {"completed", "blocked", "pending", "cancelled"}
CHANNEL_KEYS = {
    "carte": "payment.channel_card",
    "en_ligne": "payment.channel_online",
    "virement": "payment.channel_transfer",
    "retrait": "payment.channel_atm",
    "prelevement": "payment.channel_direct_debit",
}
ALERT_KIND_KEYS = {
    "fraude": "kind.fraude",
    "anomalie": "kind.anomalie",
    "securite": "kind.securite",
    "info": "kind.info",
}

#: Styles visuels (indépendants de la langue).
DECISION_STYLES = {"accept": "nx-accept", "verify": "nx-verify", "block": "nx-block"}
ALERT_SEVERITY_STYLES = {
    "critique": "nx-block",
    "eleve": "nx-verify",
    "moyen": "nx-info",
    "faible": "nx-neutral",
}
RECOMMENDATION_STYLES = {"securite": "nx-verify", "epargne": "nx-accept", "budget": "nx-info"}


# ---------------------------------------------------------------------------
# Nombres, dates
# ---------------------------------------------------------------------------
def money(value: float | None, ctx: AppContext) -> str:
    """Montant dans la devise d'affichage, ou en EUR si la conversion est indisponible."""
    amount = float(value or 0.0)
    currency = (ctx.currency or "EUR").upper()
    if currency != "EUR":
        converted = fx.convert(amount, "EUR", currency)
        if converted is not None:
            return user_prefs.format_money(converted, currency, ctx.locale)
    return user_prefs.format_money(amount, "EUR", ctx.locale)


def signed_money(value: float, ctx: AppContext) -> str:
    """Montant signé : +780,00 € / −120,50 € (format localisé)."""
    if value < 0:
        return "−" + money(abs(value), ctx)
    return "+" + money(value, ctx)


def pct(value: float | None, ctx: AppContext, digits: int = 1) -> str:
    return user_prefs.format_percent(value, ctx.locale, digits)


def date(value: datetime | None, ctx: AppContext, with_time: bool = True) -> str:
    if value is None:
        return ctx.ui("common.none")
    return user_prefs.format_datetime(value, ctx.timezone, ctx.locale, with_time)


def conversion_note(ctx: AppContext) -> str | None:
    """
    Note affichée quand la devise d'affichage n'est pas la devise de base.

    Aucune conversion n'est inventée : si aucune source fiable n'est
    configurée, l'interface l'indique et affiche les montants en EUR.
    """
    currency = (ctx.currency or "EUR").upper()
    if currency == "EUR":
        return None
    converter = fx.get_converter()
    if converter is None:
        return ctx.ui("region.currency_unavailable", currency=currency)
    return ctx.ui("region.currency_converted", source=converter.name)


# ---------------------------------------------------------------------------
# Libellés issus des services
# ---------------------------------------------------------------------------
def status_label(value: str | None, ctx: AppContext) -> str:
    code = value or ""
    return t(f"status.{code}", ctx.lang) if code in STATUS_CODES else code


def decision_label(value: str | None, ctx: AppContext) -> str:
    code = value or ""
    return t(f"decision.{code}", ctx.lang) if code in DECISION_CODES else code


def decision_short(value: str | None, ctx: AppContext) -> str:
    code = value or ""
    if code in DECISION_CODES:
        return t(f"decision.{code}_short", ctx.lang)
    return code


def channel_label(value: str | None, ctx: AppContext) -> str:
    key = CHANNEL_KEYS.get(value or "")
    return ctx.ui(key) if key else (value or "")


def severity_label(value: str | None, ctx: AppContext) -> str:
    code = value or ""
    return t(f"severity.{code}", ctx.lang) if code in ALERT_SEVERITY_STYLES else code


def alert_kind_label(value: str | None, ctx: AppContext) -> str:
    key = ALERT_KIND_KEYS.get(value or "")
    return ctx.ui(key) if key else (value or "")


def factor_label(value: str | None, ctx: AppContext, fallback: str = "") -> str:
    """Libellé d'un facteur de risque (code stable -> traduction, sinon repli)."""
    code = value or ""
    translated = t(f"factor.{code}", ctx.lang)
    if translated != f"factor.{code}":
        return translated
    return fallback or code


__all__ = [
    "ALERT_SEVERITY_STYLES",
    "DECISION_STYLES",
    "RECOMMENDATION_STYLES",
    "alert_kind_label",
    "channel_label",
    "conversion_note",
    "date",
    "decision_label",
    "decision_short",
    "factor_label",
    "money",
    "pct",
    "severity_label",
    "signed_money",
    "status_label",
]
