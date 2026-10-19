"""
Tableau de bord : solde, dépenses du mois, répartition par catégorie,
évolution quotidienne et dernières alertes.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db, now_utc
from app.deps import get_current_user
from app.models import Alert, Transaction, User
from app.schemas import CategoryTotal, DailyTotal, DashboardOut, TransactionOut, UserOut
from app.services.behavior import category_breakdown, daily_series, get_profile, to_local
from app.services.ledger import (
    accounts_of,
    balance_history,
    completed_query,
    month_start_local,
    period_totals,
)

router = APIRouter(prefix="/api/dashboard", tags=["tableau de bord"])

# Nombre de jours utilisés pour la répartition par catégorie
CATEGORY_WINDOW_DAYS = 30


@router.get("", response_model=DashboardOut)
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DashboardOut:
    """Agrège toutes les données affichées sur la page d'accueil."""
    checking, savings = accounts_of(db, user)
    profile = get_profile(db, user)

    # --- Mois en cours (dépenses / revenus) -------------------------------
    month_spending, month_income = period_totals(db, user, month_start_local())

    # --- Fenêtre glissante de 30 jours : catégories et série quotidienne --
    window_transactions = list(
        db.scalars(completed_query(user).where(Transaction.ts >= now_utc() - timedelta(days=CATEGORY_WINDOW_DAYS))).all()
    )
    categories = [CategoryTotal(**row) for row in category_breakdown(window_transactions)]
    daily = [DailyTotal(**row) for row in daily_series(window_transactions, days=7)]

    # --- Dernières opérations --------------------------------------------
    recent_transactions = list(
        db.scalars(completed_query(user).order_by(Transaction.ts.desc()).limit(8)).all()
    )

    # --- Alertes ----------------------------------------------------------
    recent_alerts = list(
        db.scalars(select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc()).limit(6)).all()
    )
    unread_alerts = int(
        db.scalar(select(func.count(Alert.id)).where(Alert.user_id == user.id, Alert.read_at.is_(None))) or 0
    )

    subscriptions = profile.get("subscriptions") or []

    return DashboardOut(
        user=UserOut.model_validate(user),
        checking_balance=round(float(checking.balance if checking else 0.0), 2),
        savings_balance=round(float(savings.balance if savings else 0.0), 2),
        month_spending=month_spending,
        month_income=month_income,
        month_net=round(month_income - month_spending, 2),
        balance_history=balance_history(db, checking, days=30) if checking else [],
        categories=categories,
        daily=daily,
        subscriptions_monthly=round(sum(row["amount"] for row in subscriptions), 2),
        alerts_unread=unread_alerts,
        recent_transactions=[TransactionOut.model_validate(transaction) for transaction in recent_transactions],
        recent_alerts=[
            {
                "id": alert.id,
                "kind": alert.kind,
                "severity": alert.severity,
                "title": alert.title,
                "message": alert.message,
                "created_at": to_local(alert.created_at).isoformat(),
                "read": alert.read_at is not None,
            }
            for alert in recent_alerts
        ],
    )
