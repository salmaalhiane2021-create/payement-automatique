"""
Accès aux opérations : historique, soldes reconstitués, totaux de période.

Ces fonctions sont partagées par le tableau de bord, la liste des transactions
et le centre de sécurité, afin de garder les routeurs fins.

Règle importante : une opération **bloquée** n'a pas eu lieu (aucun mouvement
de fonds). Elle est donc exclue des soldes, des totaux et du profil
comportemental, mais elle reste visible dans l'historique avec un statut
explicite et elle alimente les alertes de sécurité.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import now_utc
from app.models import Account, Transaction, User
from app.services.behavior import NON_SUBSCRIPTION_MERCHANTS, to_local

COMPLETED = "completed"


def base_query(user: User):
    """Requête de base sur les opérations d'un utilisateur."""
    return select(Transaction).where(Transaction.user_id == user.id)


def completed_query(user: User):
    """Opérations réellement exécutées (mouvement de fonds effectif)."""
    return base_query(user).where(Transaction.status == COMPLETED)


def fetch_transactions(
    db: Session,
    user: User,
    days: int | None = None,
    category: str | None = None,
    search: str | None = None,
    only_flagged: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Transaction], int]:
    """Liste paginée des opérations avec filtres facultatifs."""
    statement = base_query(user)

    if days:
        statement = statement.where(Transaction.ts >= now_utc() - timedelta(days=int(days)))
    if category:
        statement = statement.where(Transaction.category == category)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(Transaction.merchant.ilike(pattern), Transaction.label.ilike(pattern), Transaction.city.ilike(pattern))
        )
    if only_flagged:
        statement = statement.where(or_(Transaction.is_fraud.is_(True), Transaction.anomaly_score >= 0.5))

    total = int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    items = list(
        db.scalars(statement.order_by(Transaction.ts.desc()).limit(max(1, min(limit, 200))).offset(max(0, offset))).all()
    )
    return items, total


def accounts_of(db: Session, user: User) -> tuple[Account | None, Account | None]:
    """Retourne (compte courant, compte épargne)."""
    checking = db.scalar(select(Account).where(Account.user_id == user.id, Account.kind == "courant").order_by(Account.id))
    savings = db.scalar(select(Account).where(Account.user_id == user.id, Account.kind == "epargne").order_by(Account.id))
    return checking, savings


def period_totals(db: Session, user: User, since: datetime) -> tuple[float, float]:
    """(dépenses, revenus) exécutés depuis une date."""
    transactions = list(db.scalars(completed_query(user).where(Transaction.ts >= since)).all())
    spending = sum(abs(txn.amount) for txn in transactions if txn.amount < 0)
    income = sum(txn.amount for txn in transactions if txn.amount > 0)
    return round(spending, 2), round(income, 2)


def month_start_local(months_back: int = 0) -> datetime:
    """
    Premier instant du mois courant (ou d'un mois antérieur) exprimé en UTC naïf.

    Le calcul est fait en heure locale pour que « le mois » corresponde à celui
    de l'utilisateur, puis converti en UTC : il est ensuite comparable aux
    horodatages stockés en base.
    """
    local_now = to_local(now_utc())
    first = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if months_back:
        month_index = (first.month - 1) - months_back
        year = first.year + month_index // 12
        month = month_index % 12 + 1
        first = first.replace(year=year, month=month, day=1)
    return first.astimezone(timezone.utc).replace(tzinfo=None)


def balance_history(db: Session, account: Account, days: int = 30) -> list[float]:
    """
    Reconstitue le solde de fin de journée sur les `days` derniers jours.

    Le solde actuel est connu : on remonte le temps en soustrayant les
    opérations postérieures, ce qui donne l'évolution exacte du compte.
    """
    today_local = to_local(now_utc()).date()
    window_start = now_utc() - timedelta(days=days)

    transactions = list(
        db.scalars(
            select(Transaction)
            .where(
                Transaction.account_id == account.id,
                Transaction.status == COMPLETED,
                Transaction.ts >= window_start,
            )
            .order_by(Transaction.ts.asc())
        ).all()
    )

    # Solde au début de la fenêtre = solde actuel - somme des mouvements de la fenêtre
    balance = float(account.balance) - sum(txn.amount for txn in transactions)

    per_day: dict[str, float] = {}
    for txn in transactions:
        key = to_local(txn.ts).date().isoformat()
        per_day[key] = per_day.get(key, 0.0) + txn.amount

    points: list[float] = []
    for offset in range(days - 1, -1, -1):
        key = (today_local - timedelta(days=offset)).isoformat()
        balance += per_day.get(key, 0.0)
        points.append(round(balance, 2))
    return points


def flagged_transactions(db: Session, user: User, limit: int = 10, min_score: float = 0.45) -> list[Transaction]:
    """
    Opérations les plus atypiques (fraudes étiquetées ou score IA élevé).

    Sont exclus : les encaissements (salaire, virements reçus) et les charges
    régulières identifiées (loyer, retraits) — le modèle d'anomalie décrit le
    comportement de **dépense ponctuelle**, pas les prélèvements attendus.
    """
    rows = list(
        db.scalars(
            base_query(user)
            .where(
                Transaction.amount < 0,
                or_(Transaction.is_fraud.is_(True), Transaction.anomaly_score >= min_score),
            )
            .order_by(Transaction.anomaly_score.desc(), Transaction.ts.desc())
            .limit(max(limit, limit * 3))
        ).all()
    )
    cleaned = [txn for txn in rows if txn.merchant not in NON_SUBSCRIPTION_MERCHANTS]
    return cleaned[:limit]
