"""Page « Tableau de bord » (affichage uniquement, services partagés)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.database import now_utc
from app.models import Alert, Transaction
from app.services.alerts import unread_alert_count
from app.services.behavior import category_breakdown, daily_series, get_profile, to_local
from app.services.ledger import (
    accounts_of,
    balance_history,
    completed_query,
    month_start_local,
    period_totals,
)

from streamlit_ui.components import badge, hero, kpis
from streamlit_ui.context import AppContext
from streamlit_ui.formatting import (
    ALERT_SEVERITY_STYLES,
    date,
    money,
    pct,
    severity_label,
    signed_money,
    status_label,
)


def render(ctx: AppContext) -> None:
    db, user = ctx.db, ctx.user

    checking, savings = accounts_of(db, user)
    checking_balance = float(checking.balance if checking else 0.0)
    savings_balance = float(savings.balance if savings else 0.0)
    profile = get_profile(db, user)

    month_spending, month_income = period_totals(db, user, month_start_local())
    month_net = round(month_income - month_spending, 2)
    unread = unread_alert_count(db, user.id)

    subtitle = ctx.ui("dashboard.subtitle", date=date(now_utc(), ctx, with_time=False))
    if unread:
        subtitle += " " + ctx.ui("dashboard.unread", count=unread)
    hero(
        ctx.ui("dashboard.kicker"),
        ctx.ui("dashboard.greeting", name=user.full_name.split()[0]),
        subtitle,
    )

    # --- Indicateurs clés -------------------------------------------------
    kpis(
        [
            {
                "label": ctx.ui("dashboard.kpi.balance"),
                "value": money(checking_balance, ctx),
                "delta": ctx.ui("dashboard.kpi.balance_delta", amount=signed_money(month_net, ctx)),
                "tone": "up" if month_net >= 0 else "down",
            },
            {"label": ctx.ui("dashboard.kpi.savings"), "value": money(savings_balance, ctx)},
            {
                "label": ctx.ui("dashboard.kpi.spending"),
                "value": money(month_spending, ctx),
                "delta": (
                    ctx.ui("dashboard.kpi.spending_share", pct=pct(month_spending / month_income, ctx))
                    if month_income
                    else None
                ),
                "tone": "flat",
            },
            {"label": ctx.ui("dashboard.kpi.income"), "value": money(month_income, ctx)},
        ]
    )

    st.markdown("")

    # --- Fenêtre glissante de 30 jours -----------------------------------
    window_transactions = list(
        db.scalars(
            completed_query(user).where(Transaction.ts >= now_utc() - timedelta(days=30))
        ).all()
    )
    categories = category_breakdown(window_transactions)
    daily = daily_series(window_transactions, days=7, timezone_name=ctx.timezone)

    left, right = st.columns([1.45, 1])

    with left:
        st.markdown(f"##### {ctx.ui('dashboard.balance_evolution')}")
        if checking:
            history = balance_history(db, checking, days=30)
            dates = [
                (to_local(now_utc(), ctx.timezone).date() - timedelta(days=29 - index)).strftime("%d/%m")
                for index in range(30)
            ]
            st.line_chart(
                pd.DataFrame({"Date": dates, ctx.ui("dashboard.balance_series"): history}),
                x="Date",
                y=ctx.ui("dashboard.balance_series"),
                height=250,
            )
        else:
            st.info(ctx.ui("dashboard.no_account"))

    with right:
        st.markdown(f"##### {ctx.ui('dashboard.spending_categories')}")
        if categories:
            max_total = max(row["total"] for row in categories[:6]) or 1.0
            for row in categories[:6]:
                width = 100.0 * row["total"] / max_total
                st.markdown(
                    f'<div class="nx-cat-row"><span>{row["category"]}</span>'
                    f'<strong>{money(row["total"], ctx)}</strong></div>'
                    f'<div class="nx-cat-track"><div class="nx-cat-fill" style="width:{width:.1f}%"></div></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption(ctx.ui("dashboard.no_spending"))

    st.markdown("")

    # --- Série quotidienne et abonnements --------------------------------
    left, right = st.columns([1.45, 1])
    with left:
        st.markdown(f"##### {ctx.ui('dashboard.daily_spending')}")
        daily_frame = pd.DataFrame(
            {
                "Date": [row["date"][5:].replace("-", "/") for row in daily],
                ctx.ui("dashboard.daily_series"): [row["debit"] for row in daily],
            }
        )
        st.bar_chart(daily_frame, x="Date", y=ctx.ui("dashboard.daily_series"), height=220)

    with right:
        st.markdown(f"##### {ctx.ui('dashboard.subscriptions')}")
        subscriptions = profile.get("subscriptions") or []
        if subscriptions:
            total_subscriptions = sum(row["amount"] for row in subscriptions)
            st.metric(ctx.ui("dashboard.subscriptions_total"), money(total_subscriptions, ctx))
            for row in subscriptions[:5]:
                st.markdown(
                    f'<div class="nx-cat-row"><span>{row["merchant"]}</span>'
                    f'<strong>{money(row["amount"], ctx)}</strong></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.caption(ctx.ui("dashboard.no_subscriptions"))

    st.markdown("")

    # --- Dernières opérations et alertes ---------------------------------
    left, right = st.columns([1.55, 1])
    with left:
        st.markdown(f"##### {ctx.ui('dashboard.recent_operations')}")
        recent = list(
            db.scalars(completed_query(user).order_by(Transaction.ts.desc()).limit(8)).all()
        )
        if recent:
            frame = pd.DataFrame(
                [
                    {
                        ctx.ui("transactions.date"): date(txn.ts, ctx),
                        ctx.ui("transactions.merchant"): txn.merchant,
                        ctx.ui("transactions.category"): txn.category,
                        ctx.ui("transactions.amount"): signed_money(txn.amount, ctx),
                        ctx.ui("transactions.status"): status_label(txn.status, ctx),
                    }
                    for txn in recent
                ]
            )
            st.dataframe(frame, hide_index=True, use_container_width=True)
        else:
            st.caption(ctx.ui("dashboard.no_transactions"))

    with right:
        st.markdown(f"##### {ctx.ui('dashboard.recent_alerts')}")
        alerts = list(
            db.scalars(
                select(Alert)
                .where(Alert.user_id == user.id)
                .order_by(Alert.created_at.desc())
                .limit(4)
            ).all()
        )
        if alerts:
            for alert in alerts:
                severity = severity_label(alert.severity, ctx)
                severity_css = ALERT_SEVERITY_STYLES.get(alert.severity, "nx-neutral")
                unread_marker = (
                    f'<span class="nx-pill nx-info" style="margin-left:.35rem;">{ctx.ui("common.unread")}</span>'
                    if alert.read_at is None
                    else ""
                )
                st.markdown(
                    f'<div class="nx-card">{badge(severity, severity_css)} '
                    f"<strong>{alert.title}</strong>{unread_marker}"
                    f'<div class="nx-muted">{date(alert.created_at, ctx)}</div>'
                    f'<div style="font-size:.86rem;color:#c7d3e4;margin-top:.25rem;">'
                    f"{alert.message}</div></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption(ctx.ui("dashboard.no_alerts"))


__all__ = ["render"]
