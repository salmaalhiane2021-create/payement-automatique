"""Page « Transactions » : historique, filtres, détail d'une opération."""

from __future__ import annotations

import html
import math

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.models import Transaction
from app.services.ledger import fetch_transactions

from streamlit_ui.components import hero, kpis
from streamlit_ui.context import AppContext
from streamlit_ui.formatting import (
    channel_label,
    date,
    decision_label,
    factor_label,
    money,
    signed_money,
    status_label,
)

#: Périodes proposées : (clé de traduction, nombre de jours ou None pour tout).
PERIODS: list[tuple[str, int | None]] = [
    ("transactions.period_7", 7),
    ("transactions.period_30", 30),
    ("transactions.period_90", 90),
    ("transactions.period_year", 365),
    ("transactions.period_all", None),
]


def render(ctx: AppContext) -> None:
    db, user = ctx.db, ctx.user

    hero(
        ctx.ui("transactions.kicker"),
        ctx.ui("transactions.title"),
        ctx.ui("transactions.subtitle"),
    )

    categories = [
        row
        for row in db.scalars(
            select(Transaction.category)
            .where(Transaction.user_id == user.id)
            .distinct()
            .order_by(Transaction.category)
        ).all()
    ]

    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([1, 1, 1.5, 1])
    with filter_col1:
        period_key = st.selectbox(
            ctx.ui("transactions.period"),
            [key for key, _ in PERIODS],
            index=1,
            format_func=lambda key: ctx.ui(key),
        )
    with filter_col2:
        category = st.selectbox(
            ctx.ui("transactions.category"),
            [None] + categories,
            format_func=lambda value: ctx.ui("common.all") if value is None else value,
        )
    with filter_col3:
        search = st.text_input(
            ctx.ui("transactions.search"),
            placeholder=ctx.ui("transactions.search_placeholder"),
        )
    with filter_col4:
        only_flagged = st.checkbox(ctx.ui("transactions.only_flagged"))

    period_days = dict(PERIODS)[period_key]

    # Toute modification de filtre ramène à la première page.
    signature = (period_days, category, search.strip(), only_flagged)
    if st.session_state.get("txn_signature") != signature:
        st.session_state["txn_signature"] = signature
        st.session_state["txn_page"] = 0

    page = int(st.session_state.get("txn_page", 0))
    limit = 25
    items, total = fetch_transactions(
        db,
        user,
        days=period_days,
        category=category,
        search=search.strip() or None,
        only_flagged=only_flagged,
        limit=limit,
        offset=page * limit,
    )
    page_count = max(1, math.ceil(total / limit))
    page = min(page, page_count - 1)
    st.session_state["txn_page"] = page

    st.caption(ctx.ui("transactions.page_info", total=total, page=page + 1, pages=page_count))

    if items:
        frame = pd.DataFrame(
            [
                {
                    ctx.ui("transactions.date"): date(txn.ts, ctx),
                    ctx.ui("transactions.merchant"): txn.merchant,
                    ctx.ui("transactions.category"): txn.category,
                    ctx.ui("transactions.city"): txn.city,
                    ctx.ui("transactions.amount"): signed_money(txn.amount, ctx),
                    ctx.ui("transactions.risk"): int(round(float(txn.risk_score or 0.0))),
                    ctx.ui("transactions.ai_score"): float(txn.anomaly_score or 0.0),
                    ctx.ui("transactions.status"): status_label(txn.status, ctx),
                    ctx.ui("transactions.decision"): decision_label(txn.decision, ctx),
                }
                for txn in items
            ]
        )
        st.dataframe(
            frame,
            hide_index=True,
            use_container_width=True,
            column_config={
                ctx.ui("transactions.risk"): st.column_config.ProgressColumn(
                    ctx.ui("transactions.risk"), min_value=0, max_value=100, format="%d"
                ),
                ctx.ui("transactions.ai_score"): st.column_config.NumberColumn(format="%.2f"),
            },
        )
    else:
        st.info(ctx.ui("transactions.none_match"))

    navigation_left, navigation_middle, navigation_right = st.columns([1, 2, 1])
    if navigation_left.button(
        ctx.ui("transactions.previous"), disabled=page <= 0, use_container_width=True
    ):
        st.session_state["txn_page"] = page - 1
        st.rerun()
    if navigation_right.button(
        ctx.ui("transactions.next"), disabled=page >= page_count - 1, use_container_width=True
    ):
        st.session_state["txn_page"] = page + 1
        st.rerun()

    # --- Détail d'une opération ------------------------------------------
    if not items:
        return

    st.markdown("---")
    st.markdown(f"##### {ctx.ui('transactions.inspect')}")
    options = {
        f"{date(txn.ts, ctx)} — {txn.merchant} — {money(txn.amount, ctx)}": txn.id for txn in items
    }
    selected = st.selectbox(
        ctx.ui("transactions.operation"),
        list(options),
        index=None,
        placeholder=ctx.ui("transactions.choose"),
    )
    if not selected:
        return

    transaction = db.get(Transaction, options[selected])
    if transaction is None or transaction.user_id != user.id:
        return

    meta = transaction.meta or {}
    kpis(
        [
            {"label": ctx.ui("transactions.amount"), "value": money(abs(transaction.amount), ctx)},
            {
                "label": ctx.ui("transactions.risk"),
                "value": f"{float(transaction.risk_score or 0):.0f}/100",
            },
            {
                "label": ctx.ui("transactions.ai_score"),
                "value": f"{float(transaction.anomaly_score or 0):.2f}",
            },
            {
                "label": ctx.ui("transactions.status"),
                "value": status_label(transaction.status, ctx),
            },
        ]
    )

    st.markdown(
        f"**{ctx.ui('transactions.decision')} :** {decision_label(transaction.decision, ctx)} · "
        f"**{ctx.ui('transactions.channel')} :** {channel_label(transaction.channel, ctx)} · "
        f"**{ctx.ui('transactions.location')} :** {transaction.city}, {transaction.country}"
    )

    factors = meta.get("facteurs") or []
    if factors:
        factors_frame = pd.DataFrame(
            [
                {
                    ctx.ui("transactions.factor"): factor_label(
                        factor.get("code"), ctx, fallback=str(factor.get("label", ""))
                    ),
                    ctx.ui("transactions.detail"): factor.get("detail", ""),
                    ctx.ui("transactions.points"): factor.get("weight", 0),
                }
                for factor in factors
            ]
        )
        st.dataframe(
            factors_frame,
            hide_index=True,
            use_container_width=True,
            column_config={
                ctx.ui("transactions.points"): st.column_config.NumberColumn(format="%+.1f"),
            },
        )
    if meta.get("explication"):
        st.caption(meta["explication"])
    if meta.get("fraude_simulee"):
        st.warning(ctx.ui("transactions.fraud_simulated", scenario=meta["fraude_simulee"]))


__all__ = ["render"]
