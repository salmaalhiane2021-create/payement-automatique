"""
Écran de connexion.

La vérification du mot de passe, l'anti-énumération, le verrouillage et la
journalisation vivent dans `app/services/auth.py` — partagé avec l'API FastAPI.
Cette page ne fait qu'appeler le service et afficher le résultat.
"""

from __future__ import annotations

import html

import streamlit as st

from app.i18n import LANGUAGE_LABELS, normalize_language, t
from app.services.auth import authenticate

from streamlit_ui.context import language_selector
from streamlit_ui.demo import DEMO_ACCOUNTS, DEMO_EMAIL, DEMO_PASSWORD, DEMO_PIN


def render_login(db) -> None:
    """Affiche l'écran de connexion et gère la session Streamlit."""
    lang = normalize_language(st.session_state.get("ui_lang") or "fr")

    top_left, top_right = st.columns([5, 1])
    with top_right:
        lang = language_selector(lang, label=t("nav.language", lang))

    left, right = st.columns([1.15, 1], gap="large")

    with left:
        st.markdown(
            '<div class="nx-brand"><span class="nx-logo">N</span>'
            '<div class="nx-brand-title">NexPay <span>AI</span>'
            f'<small>{html.escape(t("app.brand_subtitle", lang))}</small></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<h1 style="font-size:2.4rem;letter-spacing:-.04em;margin:.9rem 0 .4rem;">'
            f"{html.escape(t('login.headline', lang))}</h1>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p style="color:#9fb0c8;font-size:1.02rem;max-width:520px;">'
            f"{html.escape(t('login.intro', lang))}</p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"""
<div class="nx-card" style="margin-top:1rem;">
  <strong>{html.escape(t('login.feature_otp_title', lang))}</strong>
  <div class="nx-muted">{html.escape(t('login.feature_otp_text', lang))}</div>
</div>
<div class="nx-card">
  <strong>{html.escape(t('login.feature_ai_title', lang))}</strong>
  <div class="nx-muted">{html.escape(t('login.feature_ai_text', lang))}</div>
</div>
<div class="nx-card">
  <strong>{html.escape(t('login.feature_decision_title', lang))}</strong>
  <div class="nx-muted">{html.escape(t('login.feature_decision_text', lang))}</div>
</div>
            """,
            unsafe_allow_html=True,
        )
        st.caption(t("login.simulation_notice", lang))

    with right:
        with st.container(border=True):
            st.markdown(f"### {t('login.title', lang)}")
            st.caption(t("login.prefilled", lang))

            with st.form("login_form"):
                email = st.text_input(t("login.email", lang), value=DEMO_EMAIL, placeholder="vous@exemple.fr")
                password = st.text_input(
                    t("login.password", lang), type="password", value=DEMO_PASSWORD, placeholder="••••••••"
                )
                submitted = st.form_submit_button(
                    t("login.submit", lang), type="primary", use_container_width=True
                )

            if submitted:
                result = authenticate(
                    db,
                    email.strip(),
                    password,
                    ip_address="streamlit",
                    user_agent="streamlit",
                    lang=lang,
                )
                if result.ok and result.user is not None:
                    st.session_state["user_id"] = result.user.id
                    st.session_state["ui_lang"] = normalize_language(result.user.locale)
                    st.session_state.pop("prefill_password", None)
                    st.rerun()
                elif result.status == "locked":
                    st.error(result.message)
                elif result.status == "inactive":
                    st.error(result.message)
                else:
                    st.error(result.message)
                    if result.attempts_left is not None:
                        st.caption(f"{result.attempts_left} / 5")

            with st.expander(t("login.demo_accounts", lang)):
                st.markdown(
                    f"**{t('login.password', lang)} :** `{DEMO_PASSWORD}` — "
                    f"**{t('payment.pin_label', lang)} :** `{DEMO_PIN}`\n\n"
                    + "\n".join(f"- `{mail}` — {name} ({note})" for mail, name, note in DEMO_ACCOUNTS)
                )
                st.caption(t("login.demo_caption", lang))


__all__ = ["render_login"]
