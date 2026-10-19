"""
Coquille applicative : barre latérale, navigation et préférences régionales.

La mise à jour des préférences passe par `app/services/auth.py` (validation,
journalisation) : cette page ne fait que collecter la saisie.
"""

from __future__ import annotations

import html

import streamlit as st

from app.config import settings
from app.i18n import LANGUAGE_LABELS, normalize_language
from app.services import user_prefs
from app.services.alerts import unread_alert_count
from app.services.auth import update_user_profile

from streamlit_ui.components import brand
from streamlit_ui.context import AppContext, language_selector
from streamlit_ui.formatting import conversion_note
from streamlit_ui.pages import dashboard, model, payment, security, transactions

#: Registre des pages : clé stable (pour la navigation) -> fonction d'affichage.
PAGE_RENDERERS = {
    "dashboard": dashboard.render,
    "transactions": transactions.render,
    "payment": payment.render,
    "security": security.render,
    "model": model.render,
}


def _region_form(ctx: AppContext) -> None:
    """Formulaire « Profil & région » (pays, ville, téléphone, locale, fuseau, devise)."""
    db, user = ctx.db, ctx.user

    locales = user_prefs.locale_options()
    locale_codes = [code for code, _ in locales]
    locale_labels = dict(locales)
    default_locale = user.locale if user.locale in locale_codes else locale_codes[0]

    countries = user_prefs.country_options(user.locale)
    country_codes = [code for code, _ in countries]
    country_labels = dict(countries)
    default_country = user.country_code if user.country_code in country_codes else country_codes[0]

    currencies = user_prefs.currency_options(user.locale)
    currency_codes = [code for code, _ in currencies]
    currency_labels = dict(currencies)
    default_currency = user.currency if user.currency in currency_codes else "EUR"

    zones = user_prefs.timezone_options()
    default_zone = user.timezone if user.timezone in zones else zones[0]

    note = conversion_note(ctx)
    if note:
        st.caption(note)
    st.caption(ctx.ui("region.caption"))
    st.caption(ctx.ui("region.simulation_amounts"))

    with st.form("region_form"):
        locale = st.selectbox(
            ctx.ui("region.locale"),
            locale_codes,
            index=locale_codes.index(default_locale),
            format_func=lambda code: locale_labels.get(code, code),
        )
        zone = st.selectbox(
            ctx.ui("region.timezone"),
            zones,
            index=zones.index(default_zone),
        )
        currency = st.selectbox(
            ctx.ui("region.currency"),
            currency_codes,
            index=currency_codes.index(default_currency),
            format_func=lambda code: f"{code} — {currency_labels.get(code, code)}",
        )
        country = st.selectbox(
            ctx.ui("region.country"),
            country_codes,
            index=country_codes.index(default_country),
            format_func=lambda code: country_labels.get(code, code),
        )
        city = st.text_input(ctx.ui("region.city"), value=user.city)
        phone = st.text_input(ctx.ui("region.phone"), value=user.phone)
        saved = st.form_submit_button(ctx.ui("region.save"), use_container_width=True)

    if not saved:
        return

    ok, message = update_user_profile(
        db,
        user,
        city=city,
        country_code=country,
        phone=phone,
        locale=locale,
        timezone=zone,
        currency=currency,
        ip_address="streamlit",
        lang=ctx.lang,
    )
    if not ok:
        st.error(message)
        return
    st.session_state["ui_lang"] = normalize_language(locale)
    st.success(
        ctx.ui(
            "region.saved",
            lang=LANGUAGE_LABELS.get(normalize_language(locale), locale),
            tz=zone,
            currency=currency,
        )
    )
    st.rerun()


def render_shell(ctx: AppContext) -> None:
    """Barre latérale (utilisateur, langue, région, navigation) puis page active."""
    unread = unread_alert_count(ctx.db, ctx.user.id)

    with st.sidebar:
        st.markdown(brand(ctx), unsafe_allow_html=True)
        st.caption(ctx.ui("app.prototype"))
        st.markdown("---")
        st.markdown(f"**{html.escape(ctx.user.full_name)}**")
        st.caption(ctx.user.email)
        if unread:
            st.warning(ctx.ui("nav.unread_alerts", count=unread))
        else:
            st.caption(ctx.ui("nav.no_unread"))
        st.markdown("---")

        language_selector(ctx.lang, label=ctx.ui("nav.language"))

        with st.expander(ctx.ui("nav.region")):
            _region_form(ctx)

        st.markdown("---")
        page = st.radio(
            ctx.ui("nav.navigation"),
            list(PAGE_RENDERERS),
            index=0,
            format_func=lambda key: ctx.ui(f"nav.{key}"),
            label_visibility="collapsed",
            key="navigation_page",
        )
        st.markdown("---")
        st.caption(ctx.ui("nav.otp_ttl", ttl=settings.otp_ttl_seconds))
        st.caption(
            ctx.ui(
                "nav.thresholds",
                accept=settings.risk_accept_max,
                verify=settings.risk_verify_max,
            )
        )
        if st.button(ctx.ui("nav.logout"), use_container_width=True):
            for key in (
                "user_id",
                "ui_lang",
                "payment_quote",
                "payment_meta",
                "payment_feedback",
                "last_payment",
            ):
                st.session_state.pop(key, None)
            st.rerun()

    note = conversion_note(ctx)
    if note:
        st.caption(note)

    PAGE_RENDERERS[page](ctx)


__all__ = ["PAGE_RENDERERS", "render_shell"]
