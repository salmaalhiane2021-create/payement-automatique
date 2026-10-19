"""Page « Modèle & données » : transparence du modèle, évaluation, cycle de vie."""

from __future__ import annotations

import html

import streamlit as st
from sqlalchemy import func, select

from app.ml.anomaly import model_card, train_detector
from app.models import Transaction, User
from app.services.alerts import log_action
from app.services.risk_engine import evaluate_decisions

from streamlit_ui.components import hero, kpis
from streamlit_ui.context import AppContext
from streamlit_ui.demo import DEMO_ACCOUNTS, DEMO_PASSWORD, DEMO_PIN
from streamlit_ui.formatting import pct


def render(ctx: AppContext) -> None:
    db, user = ctx.db, ctx.user
    card = model_card(db)
    metrics = card.get("metrics") or {}

    hero(ctx.ui("model.kicker"), ctx.ui("model.title"), ctx.ui("model.subtitle"))

    model_feedback = st.session_state.pop("model_feedback", None)
    if model_feedback:
        if model_feedback.get("kind") == "success":
            st.success(model_feedback.get("message", ""))
        else:
            st.error(model_feedback.get("message", ""))

    if not card.get("available"):
        st.warning(ctx.ui("model.not_trained"))
    if card.get("load_error"):
        st.warning(ctx.ui("model.version_mismatch", reason=card["load_error"]))

    kpis(
        [
            {
                "label": ctx.ui("model.roc_auc"),
                "value": f"{float(metrics.get('roc_auc', 0)):.3f}" if metrics else "—",
            },
            {
                "label": ctx.ui("model.precision"),
                "value": pct(metrics.get("precision"), ctx) if metrics else "—",
            },
            {
                "label": ctx.ui("model.recall"),
                "value": pct(metrics.get("recall"), ctx) if metrics else "—",
            },
            {"label": ctx.ui("model.f1"), "value": f"{float(metrics.get('f1', 0)):.3f}" if metrics else "—"},
        ]
    )
    st.caption(
        ctx.ui(
            "model.caption",
            algorithm=card.get("algorithm"),
            samples=card.get("n_samples", 0),
            features=card.get("n_features", 0),
            contamination=card.get("contamination"),
        )
        + (
            " "
            + ctx.ui(
                "model.threshold_caption",
                threshold=metrics.get("best_threshold"),
                f1=metrics.get("f1"),
                recall=metrics.get("recall"),
            )
            if metrics
            else ""
        )
    )
    st.caption(
        ctx.ui(
            "model.model_store",
            store=card.get("store"),
            version=card.get("feature_version"),
        )
    )

    with st.expander(ctx.ui("model.features"), expanded=False):
        for feature in card.get("features", []):
            st.markdown(f"- {html.escape(feature)}")
        st.caption(ctx.ui("model.features_caption"))

    st.markdown(f"### {ctx.ui('model.eval_title')}")
    st.caption(ctx.ui("model.eval_caption"))
    if st.button(ctx.ui("model.eval_button")):
        with st.spinner(ctx.ui("model.eval_running")):
            st.session_state["decision_eval"] = evaluate_decisions(db)

    decision_eval = st.session_state.get("decision_eval")
    if decision_eval:
        kpis(
            [
                {
                    "label": ctx.ui("model.detection_rate"),
                    "value": pct(decision_eval.get("taux_detection"), ctx),
                    "tone": "up",
                },
                {
                    "label": ctx.ui("model.friction_rate"),
                    "value": pct(decision_eval.get("taux_friction"), ctx),
                    "tone": "down",
                },
                {
                    "label": ctx.ui("model.frauds_evaluated"),
                    "value": str(decision_eval.get("n_fraudes", 0)),
                },
                {
                    "label": ctx.ui("model.legit_payments"),
                    "value": str(decision_eval.get("n_legitimes", 0)),
                },
            ]
        )
        st.caption(
            ctx.ui(
                "model.eval_breakdown",
                blocked=decision_eval.get("fraudes_bloquees", 0),
                escalated=decision_eval.get("fraudes_escaladees", 0),
                accepted=decision_eval.get("fraudes_acceptees", 0),
                legit=decision_eval.get("legitimes_acceptees", 0),
                legit_escalated=decision_eval.get("legitimes_escaladees", 0),
                legit_blocked=decision_eval.get("legitimes_bloques", 0),
            )
        )
        st.caption(
            ctx.ui(
                "model.eval_scores",
                fraud=decision_eval.get("score_moyen_fraude", 0),
                legit=decision_eval.get("score_moyen_legitime", 0),
            )
        )

    st.markdown(f"### {ctx.ui('model.lifecycle')}")
    st.caption(ctx.ui("model.lifecycle_caption"))
    st.caption(
        ctx.ui("model.retrain_interval", seconds=card.get("retrain_min_interval_seconds", 0))
    )
    if st.button(ctx.ui("model.retrain")):
        with st.spinner(ctx.ui("model.retraining")):
            report = train_detector(db, force=True)
        log_action(
            db,
            "reentrainement_modele",
            user.id,
            "ok" if report.get("status") == "ok" else "echec",
            report.get("message", ""),
            "streamlit",
        )
        st.session_state.pop("decision_eval", None)
        st.session_state["model_feedback"] = {
            "kind": "success" if report.get("status") == "ok" else "error",
            "message": report.get("message", ""),
        }
        st.rerun()

    st.markdown(f"### {ctx.ui('model.dataset')}")
    kpis(
        [
            {
                "label": ctx.ui("model.users"),
                "value": str(int(db.scalar(select(func.count(User.id))) or 0)),
            },
            {
                "label": ctx.ui("model.transactions"),
                "value": str(int(db.scalar(select(func.count(Transaction.id))) or 0)),
            },
            {
                "label": ctx.ui("model.frauds"),
                "value": str(
                    int(
                        db.scalar(
                            select(func.count(Transaction.id)).where(Transaction.is_fraud.is_(True))
                        )
                        or 0
                    )
                ),
            },
            {
                "label": ctx.ui("model.blocked"),
                "value": str(
                    int(
                        db.scalar(
                            select(func.count(Transaction.id)).where(Transaction.status == "blocked")
                        )
                        or 0
                    )
                ),
            },
        ]
    )

    st.caption(ctx.ui("model.storage_note"))
    with st.expander(ctx.ui("model.demo_accounts")):
        st.markdown(
            f"**{ctx.ui('login.password')} :** `{DEMO_PASSWORD}` — "
            f"**{ctx.ui('payment.pin_label')} :** `{DEMO_PIN}`\n\n"
            + "\n".join(f"- `{mail}` — {name}" for mail, name, _ in DEMO_ACCOUNTS)
        )


__all__ = ["render"]
