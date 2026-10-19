"""
Page « Paiement sécurisé » : scénarios, formulaire libre, validation OTP.

Toute la logique (analyse, défi, vérification, écritures atomiques) vit dans
`app/services/payment_flow.py` — partagée avec l'API FastAPI. Cette page ne
fait que collecter la saisie et afficher les résultats localisés.
"""

from __future__ import annotations

import secrets
from datetime import datetime

import pandas as pd
import streamlit as st

from app.config import settings
from app.database import now_utc
from app.services import payment_flow
from app.services.otp import attempts_left, seconds_remaining

from streamlit_ui.components import badge, hero, kpis
from streamlit_ui.context import AppContext
from streamlit_ui.demo import DEMO_PIN
from streamlit_ui.formatting import (
    DECISION_STYLES,
    channel_label,
    date,
    decision_short,
    factor_label,
    money,
    severity_label,
)

#: Canaux proposés dans le formulaire libre.
CHANNELS = ["carte", "en_ligne", "virement", "retrait"]

PAYMENT_STATE_KEYS = ("payment_quote", "payment_meta", "payment_feedback", "last_payment")


def _reset_payment_state() -> None:
    for key in PAYMENT_STATE_KEYS:
        st.session_state.pop(key, None)


def _payload_from_scenario(payload: dict, context: dict) -> dict:
    """Transforme un scénario de démonstration en requête de paiement."""
    data = dict(payload)
    use_known = bool(data.pop("use_known_device", False))
    declared_label = data.pop("device_label", None)

    known_devices = context.get("devices") or []
    if use_known and known_devices:
        fingerprint = known_devices[0]["fingerprint"]
        device_label = known_devices[0]["label"]
    else:
        fingerprint = f"streamlit-{secrets.token_hex(8)}"
        device_label = declared_label or "Appareil inconnu"

    city = data.get("city") or "Paris"
    return {
        "merchant": data["merchant"],
        "amount": data["amount"],
        "category": data.get("category", "Autres"),
        "city": city,
        "country": data.get("country") or (context.get("city_countries") or {}).get(city) or "",
        "channel": data.get("channel", "en_ligne"),
        "device_fingerprint": fingerprint,
        "device_label": device_label,
        "simulated_hour": data.get("simulated_hour"),
        "simulated_minute": data.get("simulated_minute"),
    }


def _run_scenario(ctx: AppContext, scenario: dict) -> None:
    """Exécute un scénario (paiement préparatoire éventuel) puis l'analyse."""
    db, user = ctx.db, ctx.user
    context = payment_flow.payment_context(db, user)
    notice = None

    prepayment = scenario.get("pre_payment")
    if prepayment:
        prep_payload = _payload_from_scenario(prepayment, context)
        prep_quote = payment_flow.quote(db, user, prep_payload, ip_address="streamlit", lang=ctx.lang)
        if prep_quote["challenge_id"] and prep_quote["simulation_code"]:
            challenge = payment_flow.load_challenge(db, user, prep_quote["challenge_id"])
            result = payment_flow.confirm(
                db,
                user,
                challenge,
                prep_quote["simulation_code"],
                ip_address="streamlit",
                lang=ctx.lang,
            )
            if result["status"] == "completed":
                notice = ctx.ui(
                    "payment.prepayment_done",
                    amount=money(prep_payload["amount"], ctx),
                    city=prep_payload["city"],
                )
            else:
                notice = ctx.ui("payment.prepayment_failed", message=result["message"])
        else:
            notice = ctx.ui("payment.prepayment_blocked")

    payload = _payload_from_scenario(scenario["payload"], context)
    result = payment_flow.quote(db, user, payload, ip_address="streamlit", lang=ctx.lang)
    st.session_state["payment_quote"] = result
    st.session_state["payment_meta"] = {
        "payload": payload,
        "amount": payload["amount"],
        "merchant": payload["merchant"],
        "city": payload["city"],
        "notice": notice,
    }
    st.session_state["payment_feedback"] = None
    st.session_state.pop("last_payment", None)
    st.rerun()


@st.fragment(run_every=1.0)
def _otp_countdown(expires_at: datetime, ttl_seconds: int, countdown_text: str, expired_text: str) -> None:
    """Compte à rebours rafraîchi chaque seconde (sans recharger la page)."""
    remaining = max(0, int((expires_at - now_utc()).total_seconds()))
    ratio = min(1.0, max(0.0, remaining / max(ttl_seconds, 1)))
    st.progress(ratio, text=countdown_text.format(seconds=remaining))
    if remaining == 0:
        st.error(expired_text)


def _render_otp_panel(ctx: AppContext, quote: dict, meta: dict) -> None:
    """Affiche le code, le compte à rebours et le formulaire de validation."""
    db, user = ctx.db, ctx.user
    challenge = payment_flow.load_challenge(db, user, quote["challenge_id"])
    if challenge is None:
        st.error(ctx.ui("payment.challenge_missing"))
        if st.button(ctx.ui("payment.new_analysis"), use_container_width=False):
            _reset_payment_state()
            st.rerun()
        return

    if challenge.consumed_at is not None:
        st.info(ctx.ui("payment.challenge_used"))
        if st.button(ctx.ui("payment.new_analysis"), use_container_width=False):
            _reset_payment_state()
            st.rerun()
        return

    remaining = seconds_remaining(challenge)
    if remaining <= 0:
        st.error(ctx.ui("payment.code_expired"))
        if st.button(ctx.ui("payment.new_code"), use_container_width=False):
            _new_code(ctx, meta)
        return

    st.markdown(f"#### {ctx.ui('payment.otp_title')}")

    if quote.get("simulation_code"):
        st.markdown(
            f'<div class="nx-card">{ctx.ui("payment.code_demo")} '
            f'<span class="nx-code">{quote["simulation_code"]}</span>'
            f'<div class="nx-muted">'
            f"{ctx.ui('payment.code_demo_note', phone=quote.get('masked_phone') or '', ttl=settings.otp_ttl_seconds)}"
            f"</div></div>",
            unsafe_allow_html=True,
        )
    else:
        st.info(
            ctx.ui(
                "payment.code_sent",
                phone=quote.get("masked_phone") or ctx.ui("common.none"),
            )
        )

    _otp_countdown(
        challenge.expires_at,
        settings.otp_ttl_seconds,
        ctx.ui("payment.countdown", seconds="{seconds}"),
        ctx.ui("payment.countdown_expired"),
    )
    st.caption(
        ctx.ui(
            "payment.attempts_left",
            left=attempts_left(challenge),
            max=settings.otp_max_attempts,
        )
    )

    if quote.get("require_pin"):
        st.warning(ctx.ui("payment.pin_required"))

    with st.form("otp_form"):
        code = st.text_input(
            ctx.ui("payment.code_label"), max_chars=6, placeholder="••••••"
        )
        pin = ""
        if quote.get("require_pin"):
            pin = st.text_input(
                ctx.ui("payment.pin_label"),
                type="password",
                max_chars=6,
                help=ctx.ui("payment.pin_help", pin=DEMO_PIN),
            )
        validated = st.form_submit_button(
            ctx.ui("payment.validate"), type="primary", use_container_width=True
        )

    action_col1, action_col2 = st.columns(2)
    if action_col1.button(ctx.ui("payment.new_code"), use_container_width=True):
        _new_code(ctx, meta)
    if action_col2.button(ctx.ui("payment.cancel"), use_container_width=True):
        payment_flow.cancel(db, challenge, user.id, ip_address="streamlit")
        st.session_state["payment_feedback"] = {
            "kind": "info",
            "message": ctx.ui("payment.cancelled"),
        }
        st.session_state.pop("payment_quote", None)
        st.session_state.pop("payment_meta", None)
        st.rerun()

    if not validated:
        return

    if not (code.isdigit() and len(code) == 6):
        st.error(ctx.ui("payment.error_code_required"))
        return
    if quote.get("require_pin") and not pin:
        st.error(ctx.ui("payment.pin_missing"))
        return

    with st.spinner(ctx.ui("payment.checking")):
        result = payment_flow.confirm(
            db,
            user,
            challenge,
            code,
            pin or None,
            ip_address="streamlit",
            lang=ctx.lang,
        )

    if result["status"] == "error":
        st.session_state["payment_feedback"] = {"kind": "error", "message": result["message"]}
        st.rerun()

    st.session_state["last_payment"] = result
    st.session_state.pop("payment_quote", None)
    st.session_state.pop("payment_meta", None)
    st.session_state["payment_feedback"] = None
    st.rerun()


def _new_code(ctx: AppContext, meta: dict) -> None:
    """Relance l'analyse pour obtenir un nouveau défi (même opération)."""
    payload = meta.get("payload")
    if not payload:
        _reset_payment_state()
        st.rerun()
    result = payment_flow.quote(ctx.db, ctx.user, payload, ip_address="streamlit", lang=ctx.lang)
    st.session_state["payment_quote"] = result
    st.session_state["payment_feedback"] = None
    st.rerun()


def render(ctx: AppContext) -> None:
    db, user = ctx.db, ctx.user

    hero(ctx.ui("payment.kicker"), ctx.ui("payment.title"), ctx.ui("payment.subtitle"))

    column1, column2, column3 = st.columns(3)
    for column, (title_key, text_key) in zip(
        (column1, column2, column3),
        (
            ("payment.step1_title", "payment.step1_text"),
            ("payment.step2_title", "payment.step2_text"),
            ("payment.step3_title", "payment.step3_text"),
        ),
    ):
        with column:
            st.markdown(
                f'<div class="nx-card"><strong>{ctx.ui(title_key)}</strong>'
                f'<div class="nx-muted">{ctx.ui(text_key)}</div></div>',
                unsafe_allow_html=True,
            )

    # --- Scénarios de démonstration --------------------------------------
    st.markdown(f"### {ctx.ui('payment.scenarios')}")
    st.caption(ctx.ui("payment.scenarios_caption"))

    scenarios = payment_flow.demo_scenarios(user)
    columns = st.columns(2)
    for index, scenario in enumerate(scenarios):
        code = scenario["code"]
        title = ctx.ui_or(f"payment.scenario.{code}.title", scenario["title"])
        description = ctx.ui_or(f"payment.scenario.{code}.description", scenario["description"])
        expected_label = decision_short(scenario["expected"], ctx)
        expected_css = DECISION_STYLES.get(scenario["expected"], "nx-neutral")
        with columns[index % 2]:
            with st.container(border=True):
                st.markdown(
                    f"<strong>{title}</strong> {badge(expected_label, expected_css)}",
                    unsafe_allow_html=True,
                )
                st.caption(description)
                if st.button(
                    ctx.ui("payment.launch_scenario"),
                    key=f"scenario_{code}",
                    use_container_width=True,
                ):
                    _run_scenario(ctx, scenario)

    # --- Formulaire libre --------------------------------------------------
    st.markdown(f"### {ctx.ui('payment.free')}")
    context = payment_flow.payment_context(db, user)
    cities = context.get("cities") or ["Paris"]
    default_city = user.city if user.city in cities else cities[0]
    city_index = cities.index(default_city)

    device_labels = [ctx.ui("payment.new_device")] + [
        f"{device['label']} ({device['platform']})" for device in context.get("devices", [])
    ]

    with st.form("payment_form"):
        form_col1, form_col2 = st.columns(2)
        with form_col1:
            merchant = st.text_input(ctx.ui("payment.merchant"), value="Café des Arts", max_chars=120)
            category = st.selectbox(ctx.ui("payment.category"), context.get("categories") or ["Autres"])
            channel = st.selectbox(
                ctx.ui("payment.channel"),
                CHANNELS,
                format_func=lambda value: channel_label(value, ctx),
            )
            hour_label = st.selectbox(
                ctx.ui("payment.hour"),
                [ctx.ui("common.current_hour")] + [f"{hour:02d} h" for hour in range(24)],
                help=ctx.ui("payment.hour_help"),
            )
        with form_col2:
            amount = st.number_input(
                ctx.ui("payment.amount", currency="EUR"),
                min_value=0.01,
                max_value=float(settings.payment_max_amount),
                value=12.50,
                step=1.0,
                format="%.2f",
            )
            city = st.selectbox(ctx.ui("payment.city"), cities, index=city_index)
            device_label = st.selectbox(ctx.ui("payment.device"), device_labels)
            st.caption(
                ctx.ui(
                    "payment.balance_available",
                    balance=money(context.get("balance"), ctx),
                    phone=context.get("masked_phone") or ctx.ui("common.none"),
                )
            )
        submitted = st.form_submit_button(
            ctx.ui("payment.analyze"), type="primary", use_container_width=True
        )

    if submitted:
        if len(merchant.strip()) < 2:
            st.error(ctx.ui("payment.merchant_error"))
        else:
            device_index = device_labels.index(device_label)
            if device_index == 0:
                fingerprint = f"streamlit-{secrets.token_hex(8)}"
                label = "Navigateur Streamlit inconnu"
            else:
                device = context["devices"][device_index - 1]
                fingerprint = device["fingerprint"]
                label = device["label"]

            payload = {
                "merchant": merchant.strip(),
                "amount": float(amount),
                "category": category,
                "city": city,
                "country": (context.get("city_countries") or {}).get(city, ""),
                "channel": channel,
                "device_fingerprint": fingerprint,
                "device_label": label,
                "simulated_hour": None if hour_label == ctx.ui("common.current_hour") else int(hour_label[:2]),
                "simulated_minute": None,
            }
            result = payment_flow.quote(db, user, payload, ip_address="streamlit", lang=ctx.lang)
            st.session_state["payment_quote"] = result
            st.session_state["payment_meta"] = {
                "payload": payload,
                "amount": payload["amount"],
                "merchant": payload["merchant"],
                "city": payload["city"],
                "notice": None,
            }
            st.session_state["payment_feedback"] = None
            st.session_state.pop("last_payment", None)
            st.rerun()

    # --- Résultat de la dernière validation --------------------------------
    last_payment = st.session_state.get("last_payment")
    if last_payment:
        st.markdown("---")
        st.success(last_payment["message"])
        kpis(
            [
                {
                    "label": ctx.ui("payment.new_balance"),
                    "value": money(last_payment.get("new_balance"), ctx),
                },
                {
                    "label": ctx.ui("transactions.risk"),
                    "value": f"{float(last_payment.get('risk_score', 0)):.0f}/100",
                },
                {
                    "label": ctx.ui("transactions.ai_score"),
                    "value": f"{float(last_payment.get('anomaly_score', 0)):.2f}",
                },
            ]
        )
        if last_payment.get("alert_id"):
            st.warning(ctx.ui("payment.atypical_alert"))
        if st.button(ctx.ui("payment.new_payment"), use_container_width=False):
            _reset_payment_state()
            st.rerun()

    # --- Analyse en cours --------------------------------------------------
    quote = st.session_state.get("payment_quote")
    if not quote:
        return

    meta = st.session_state.get("payment_meta") or {}
    st.markdown("---")
    st.markdown(f"### {ctx.ui('payment.analysis_result')}")

    if meta.get("notice"):
        st.info(meta["notice"])

    decision = quote["decision"]
    risk_score = float(quote["risk_score"])
    ai_score = float(quote["anomaly_score"])
    st.markdown(
        f'{badge(decision_short(decision, ctx), DECISION_STYLES.get(decision, "nx-neutral"))} '
        f'<span class="nx-muted">'
        f"{ctx.ui('payment.score_line', score=f'{risk_score:.0f}', ai=f'{ai_score:.2f}')}"
        f"</span>",
        unsafe_allow_html=True,
    )
    st.progress(
        min(1.0, max(0.0, risk_score / 100.0)),
        text=ctx.ui("payment.risk_label", score=f"{risk_score:.0f}"),
    )
    st.markdown(f"**{quote['summary']}**")

    if meta.get("merchant"):
        st.caption(
            ctx.ui(
                "payment.operation_line",
                amount=money(meta.get("amount"), ctx),
                merchant=meta.get("merchant", ""),
                city=meta.get("city", ""),
            )
        )

    factors = quote.get("factors") or []
    if factors:
        with st.expander(ctx.ui("payment.see_factors"), expanded=True):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            ctx.ui("transactions.factor"): factor_label(
                                factor.get("code"), ctx, fallback=str(factor.get("label", ""))
                            ),
                            ctx.ui("transactions.detail"): factor.get("detail", ""),
                            ctx.ui("transactions.points"): factor.get("weight", 0),
                            ctx.ui("transactions.severity"): severity_label(
                                factor.get("severity"), ctx
                            ),
                        }
                        for factor in factors
                    ]
                ),
                hide_index=True,
                use_container_width=True,
                column_config={
                    ctx.ui("transactions.points"): st.column_config.NumberColumn(format="%+.1f")
                },
            )

    feedback = st.session_state.get("payment_feedback")
    if feedback:
        if feedback.get("kind") == "error":
            st.error(feedback.get("message", ""))
        else:
            st.info(feedback.get("message", ""))

    if decision == "block":
        st.error(ctx.ui("payment.blocked"))
        if st.button(ctx.ui("payment.new_analysis"), use_container_width=False):
            _reset_payment_state()
            st.rerun()
        return

    _render_otp_panel(ctx, quote, meta)


__all__ = ["render"]
