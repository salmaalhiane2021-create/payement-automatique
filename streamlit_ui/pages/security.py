"""Page « Sécurité & IA » : profil comportemental, anomalies, alertes, appareils, audit."""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.database import now_utc
from app.ml.anomaly import explain
from app.ml.features import build_features, context_from_transaction
from app.models import Alert, AuditLog, Device, Transaction
from app.services import user_prefs
from app.services.alerts import log_action, mark_alert_read, unread_alert_count
from app.services.behavior import get_profile
from app.services.ledger import flagged_transactions
from app.services.recommendations import build_recommendations

from streamlit_ui.components import badge, hero, kpis
from streamlit_ui.context import AppContext
from streamlit_ui.formatting import (
    ALERT_SEVERITY_STYLES,
    RECOMMENDATION_STYLES,
    alert_kind_label,
    date,
    money,
    pct,
    severity_label,
    status_label,
)


def _profile_tab(ctx: AppContext) -> None:
    db, user = ctx.db, ctx.user
    profile = get_profile(db, user)
    stats = profile.get("amount_stats") or {}

    kpis(
        [
            {"label": ctx.ui("security.avg_basket"), "value": money(stats.get("mean"), ctx)},
            {"label": ctx.ui("security.median"), "value": money(stats.get("median"), ctx)},
            {
                "label": ctx.ui("security.monthly_avg"),
                "value": money(profile.get("average_monthly_spending"), ctx),
            },
            {"label": ctx.ui("security.analyzed"), "value": str(profile.get("count", 0))},
        ]
    )

    st.caption(
        ctx.ui(
            "security.range_caption",
            low=money(stats.get("p10"), ctx),
            high=money(stats.get("p90"), ctx),
            hour=(profile.get("typical_hours") or ["—"])[0],
            night=pct(profile.get("night_ratio"), ctx),
            online=pct(profile.get("online_ratio"), ctx),
        )
    )

    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown(f"##### {ctx.ui('security.hourly')}")
        hours_frame = pd.DataFrame(
            {
                ctx.ui("security.hour"): [f"{hour:02d}h" for hour in range(24)],
                ctx.ui("security.hourly") + " (%)": [
                    round(float(value) * 100, 2) for value in profile.get("hourly_hist", [])
                ],
            }
        )
        st.bar_chart(
            hours_frame,
            x=ctx.ui("security.hour"),
            y=ctx.ui("security.hourly") + " (%)",
            height=220,
        )
    with col_right:
        st.markdown(f"##### {ctx.ui('security.weekday')}")
        weekdays_frame = pd.DataFrame(
            {
                ctx.ui("security.day"): user_prefs.weekday_names(ctx.locale),
                ctx.ui("security.weekday") + " (%)": [
                    round(float(value) * 100, 2) for value in profile.get("weekday_hist", [])
                ],
            }
        )
        st.bar_chart(
            weekdays_frame,
            x=ctx.ui("security.day"),
            y=ctx.ui("security.weekday") + " (%)",
            height=220,
        )

    st.markdown(f"##### {ctx.ui('security.observations')}")
    for insight in profile.get("insights", []):
        st.markdown(f"- {insight}")

    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown(f"##### {ctx.ui('security.top_categories')}")
        top_categories = profile.get("top_categories") or []
        if top_categories:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            ctx.ui("transactions.category"): row["category"],
                            ctx.ui("transactions.amount"): money(row["total"], ctx),
                            ctx.ui("transactions.operations"): row["count"],
                            ctx.ui("transactions.share"): pct(row["share"], ctx),
                        }
                        for row in top_categories
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
    with col_right:
        st.markdown(f"##### {ctx.ui('security.subscriptions')}")
        subscriptions = profile.get("subscriptions") or []
        if subscriptions:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            ctx.ui("transactions.merchant"): row["merchant"],
                            ctx.ui("transactions.amount"): money(row["amount"], ctx),
                            ctx.ui("transactions.occurrences"): row["occurrences"],
                        }
                        for row in subscriptions
                    ]
                ),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption(ctx.ui("security.no_subscriptions"))

    st.markdown(f"##### {ctx.ui('security.recommendations')}")
    for recommendation in build_recommendations(db, user, profile):
        kind = recommendation["kind"]
        kind_css = RECOMMENDATION_STYLES.get(kind, "nx-neutral")
        kind_label = ctx.ui_or(f"security.kind_{kind}", kind)
        impact = (
            ctx.ui("security.impact", amount=money(recommendation["impact_eur"], ctx))
            if recommendation.get("impact_eur")
            else ""
        )
        st.markdown(
            f'<div class="nx-card">{badge(kind_label, kind_css)} '
            f'<strong>{recommendation["title"]}</strong>'
            f'<div style="font-size:.88rem;color:#c7d3e4;margin-top:.3rem;">'
            f"{recommendation['detail']}</div>"
            f'<div class="nx-muted" style="margin-top:.25rem;">'
            f"{ctx.ui('security.priority', priority=recommendation['priority'])}"
            f"{impact} · {recommendation.get('action', '')}</div></div>",
            unsafe_allow_html=True,
        )


def _anomalies_tab(ctx: AppContext) -> None:
    db, user = ctx.db, ctx.user
    profile = get_profile(db, user)
    rows = flagged_transactions(db, user, limit=10)
    st.caption(ctx.ui("security.anomalies_caption"))
    if not rows:
        st.info(ctx.ui("security.no_anomaly"))
        return

    ordered = list(
        db.scalars(
            select(Transaction)
            .where(Transaction.user_id == user.id)
            .order_by(Transaction.ts.asc())
        ).all()
    )
    previous_by_id: dict[int, Transaction | None] = {}
    previous = None
    for transaction in ordered:
        previous_by_id[transaction.id] = previous
        previous = transaction

    for transaction in rows:
        context = context_from_transaction(
            transaction, previous_by_id.get(transaction.id), timezone_name=ctx.timezone
        )
        features = build_features(context, profile)
        reasons = explain(features, context, profile)
        st.markdown(
            f'<div class="nx-card">{badge(ctx.ui("security.anomaly_badge"), "nx-block")} '
            f"<strong>{transaction.merchant}</strong> "
            f'<span class="nx-muted">{date(transaction.ts, ctx)} · '
            f"{money(abs(transaction.amount), ctx)} · {transaction.city}</span>"
            f'<div style="font-size:.86rem;color:#c7d3e4;margin-top:.35rem;">'
            f"{' '.join(reasons)}</div>"
            f'<div class="nx-muted" style="margin-top:.3rem;">'
            f"{ctx.ui('security.anomaly_scores', ai=f'{float(transaction.anomaly_score or 0):.2f}', risk=f'{float(transaction.risk_score or 0):.0f}', status=status_label(transaction.status, ctx))}"
            f"</div></div>",
            unsafe_allow_html=True,
        )


def _alerts_tab(ctx: AppContext) -> None:
    db, user = ctx.db, ctx.user
    alerts = list(
        db.scalars(select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc())).all()
    )
    unread_alerts = sum(1 for alert in alerts if alert.read_at is None)
    col1, col2 = st.columns([3, 1])
    col1.markdown(ctx.ui("security.alerts_count", total=len(alerts), unread=unread_alerts))
    if col2.button(
        ctx.ui("security.mark_all_read"), disabled=unread_alerts == 0, use_container_width=True
    ):
        for alert in alerts:
            if alert.read_at is None:
                alert.read_at = now_utc()
        db.commit()
        st.rerun()

    if not alerts:
        st.info(ctx.ui("security.no_alert"))
    for alert in alerts:
        severity_css = ALERT_SEVERITY_STYLES.get(alert.severity, "nx-neutral")
        marker = (
            f' <span class="nx-pill nx-info">{ctx.ui("common.unread")}</span>'
            if alert.read_at is None
            else f' <span class="nx-pill nx-neutral">{ctx.ui("common.read")}</span>'
        )
        st.markdown(
            f'<div class="nx-card">{badge(severity_label(alert.severity, ctx), severity_css)} '
            f"<strong>{alert.title}</strong>{marker}"
            f'<div class="nx-muted">{date(alert.created_at, ctx)} · '
            f"{alert_kind_label(alert.kind, ctx)}</div>"
            f'<div style="font-size:.87rem;color:#c7d3e4;margin-top:.3rem;">'
            f"{alert.message}</div></div>",
            unsafe_allow_html=True,
        )
        if alert.read_at is None and st.button(
            ctx.ui("security.mark_read"), key=f"alert_read_{alert.id}"
        ):
            mark_alert_read(db, user.id, alert.id)
            st.rerun()


def _devices_tab(ctx: AppContext) -> None:
    db, user = ctx.db, ctx.user
    devices = list(
        db.scalars(select(Device).where(Device.user_id == user.id).order_by(Device.last_seen.desc())).all()
    )
    st.caption(ctx.ui("security.device_caption"))
    if not devices:
        st.info(ctx.ui("security.no_device"))
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    ctx.ui("security.device"): device.label,
                    ctx.ui("security.platform"): device.platform,
                    ctx.ui("security.verified"): (
                        ctx.ui("common.yes") if device.is_trusted else ctx.ui("common.no")
                    ),
                    ctx.ui("security.last_used"): date(device.last_seen, ctx),
                }
                for device in devices
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )

    selected = st.selectbox(
        ctx.ui("security.manage"),
        devices,
        format_func=lambda device: f"{device.label} — {device.platform}",
    )
    col1, col2 = st.columns(2)
    if col1.button(
        ctx.ui("security.mark_verified"),
        disabled=selected.is_trusted,
        use_container_width=True,
    ):
        selected.is_trusted = True
        db.commit()
        log_action(
            db,
            "appareil_verifie",
            user.id,
            "ok",
            f"Appareil « {selected.label} » vérifié (Streamlit).",
            "streamlit",
        )
        st.rerun()

    confirm = st.checkbox(ctx.ui("security.confirm_revoke"))
    if col2.button(
        ctx.ui("security.revoke"),
        disabled=not confirm,
        use_container_width=True,
    ):
        label = selected.label
        db.delete(selected)
        db.commit()
        log_action(
            db,
            "appareil_revoque",
            user.id,
            "ok",
            f"Appareil « {label} » révoqué (Streamlit).",
            "streamlit",
        )
        st.rerun()


def _audit_tab(ctx: AppContext) -> None:
    db, user = ctx.db, ctx.user
    entries = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.user_id == user.id)
            .order_by(AuditLog.created_at.desc())
            .limit(40)
        ).all()
    )
    st.caption(ctx.ui("security.audit_caption"))
    if entries:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        ctx.ui("transactions.date"): date(entry.created_at, ctx),
                        ctx.ui("security.action"): entry.action,
                        ctx.ui("transactions.status"): entry.status,
                        ctx.ui("transactions.detail"): entry.detail,
                    }
                    for entry in entries
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.info(ctx.ui("security.no_audit"))


def render(ctx: AppContext) -> None:
    unread = unread_alert_count(ctx.db, ctx.user.id)
    hero(
        ctx.ui("security.kicker"),
        ctx.ui("security.title"),
        ctx.ui("security.subtitle", count=unread),
    )

    tab_profile, tab_anomalies, tab_alerts, tab_devices, tab_audit = st.tabs(
        [
            ctx.ui("security.tab_profile"),
            ctx.ui("security.tab_anomalies"),
            ctx.ui("security.tab_alerts"),
            ctx.ui("security.tab_devices"),
            ctx.ui("security.tab_audit"),
        ]
    )
    with tab_profile:
        _profile_tab(ctx)
    with tab_anomalies:
        _anomalies_tab(ctx)
    with tab_alerts:
        _alerts_tab(ctx)
    with tab_devices:
        _devices_tab(ctx)
    with tab_audit:
        _audit_tab(ctx)


__all__ = ["render"]
