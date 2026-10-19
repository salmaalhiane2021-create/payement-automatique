"""
Historique des transactions : liste filtrable, détail, catégories disponibles.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Transaction, User
from app.schemas import TransactionOut
from app.services.behavior import to_local
from app.services.ledger import fetch_transactions

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


@router.get("")
def list_transactions(
    days: int | None = Query(default=None, ge=1, le=730, description="Fenêtre en jours"),
    category: str | None = Query(default=None, max_length=40),
    search: str | None = Query(default=None, max_length=80),
    flagged: bool = Query(default=False, description="Uniquement les opérations suspectes"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Liste paginée des opérations, du plus récent au plus ancien.

    Les opérations bloquées sont incluses (statut `blocked`) : l'utilisateur
    doit voir les tentatives refusées.
    """
    items, total = fetch_transactions(
        db,
        user,
        days=days,
        category=category,
        search=search,
        only_flagged=flagged,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [TransactionOut.model_validate(item) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/categories")
def list_categories(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Catégories réellement présentes dans l'historique (pour les filtres)."""
    rows = db.scalars(
        select(Transaction.category).where(Transaction.user_id == user.id).distinct().order_by(Transaction.category)
    ).all()
    return {"categories": list(rows)}


@router.get("/{transaction_id}")
def get_transaction(
    transaction_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Détail d'une opération, avec l'explication de la décision de sécurité."""
    transaction = db.scalar(
        select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == user.id)
    )
    if transaction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opération introuvable.")

    meta = transaction.meta or {}
    return {
        "transaction": TransactionOut.model_validate(transaction),
        "date_locale": to_local(transaction.ts).isoformat(),
        "risk_score": transaction.risk_score,
        "anomaly_score": transaction.anomaly_score,
        "decision": transaction.decision,
        "facteurs": meta.get("facteurs", []),
        "explication": meta.get("explication", ""),
        "fraude_simulee": meta.get("fraude_simulee"),
    }
