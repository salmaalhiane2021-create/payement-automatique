"""
Centre de sécurité et d'intelligence artificielle.

Expose au client :

* le **profil comportemental** de l'utilisateur (habitudes de dépenses) ;
* les **anomalies détectées** dans l'historique par l'Isolation Forest ;
* les **alertes** de fraude / sécurité (fil temps réel par interrogation
  périodique toutes les 4 secondes) ;
* les **recommandations** personnalisées de sécurité et d'épargne ;
* la **fiche du modèle** (variables, métriques, ré-entraînement) ;
* la gestion des **appareils de confiance**.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db, now_utc
from app.deps import client_ip, get_current_user
from app.ml.anomaly import explain, model_card, train_detector
from app.ml.features import build_features, context_from_transaction
from app.models import Alert, AuditLog, BehavioralProfile, Device, Transaction, User
from app.schemas import (
    AlertOut,
    AnomalyOut,
    BehavioralProfileOut,
    ModelCardOut,
    RecommendationOut,
    RetrainOut,
    TransactionOut,
)
from app.services.alerts import log_action, mark_alert_read, unread_alert_count
from app.services.behavior import get_profile, to_local
from app.services.ledger import flagged_transactions
from app.services.recommendations import build_recommendations

router = APIRouter(prefix="/api/security", tags=["sécurité & IA"])


# ---------------------------------------------------------------------------
# Profil comportemental
# ---------------------------------------------------------------------------
@router.get("/profile", response_model=BehavioralProfileOut)
def behavioral_profile(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BehavioralProfileOut:
    """Profil comportemental calculé à partir de l'historique."""
    profile = get_profile(db, user)
    stats = profile.get("amount_stats") or {}
    row = db.scalar(select(BehavioralProfile).where(BehavioralProfile.user_id == user.id))

    devices = list(db.scalars(select(Device).where(Device.user_id == user.id)).all())

    return BehavioralProfileOut(
        updated_at=row.updated_at if row else now_utc(),
        total_transactions=int(profile.get("count", 0)),
        period_days=int(profile.get("period_days", 0)),
        average_amount=float(stats.get("mean", 0.0)),
        median_amount=float(stats.get("median", 0.0)),
        typical_range=[float(stats.get("p10", 0.0)), float(stats.get("p90", 0.0))],
        favorite_hour=int((profile.get("typical_hours") or [12])[0]),
        hourly_distribution=[float(value) for value in profile.get("hourly_hist", [])],
        weekday_distribution=[float(value) for value in profile.get("weekday_hist", [])],
        known_cities=list(profile.get("known_cities", [])),
        known_devices=[f"{device.label} ({device.platform})" for device in devices]
        or list(profile.get("known_devices", [])),
        known_channels=list(profile.get("known_channels", [])),
        top_categories=list(profile.get("top_categories", [])),
        top_merchants=list(profile.get("known_merchants", [])),
        monthly_average_spending=float(profile.get("average_monthly_spending", 0.0)),
        subscriptions=list(profile.get("subscriptions", [])),
        night_ratio=float(profile.get("night_ratio", 0.0)),
        online_ratio=float(profile.get("online_ratio", 0.0)),
        insights=list(profile.get("insights", [])),
    )


# ---------------------------------------------------------------------------
# Anomalies détectées
# ---------------------------------------------------------------------------
@router.get("/anomalies", response_model=list[AnomalyOut])
def anomalies(
    limit: int = Query(default=10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AnomalyOut]:
    """Opérations les plus atypiques, avec l'explication du modèle."""
    profile = get_profile(db, user)
    rows = flagged_transactions(db, user, limit=limit)

    # On reconstitue le contexte de chaque opération pour expliquer le score.
    ordered = list(
        db.scalars(select(Transaction).where(Transaction.user_id == user.id).order_by(Transaction.ts.asc())).all()
    )
    previous_by_id: dict[int, Transaction | None] = {}
    previous = None
    for transaction in ordered:
        previous_by_id[transaction.id] = previous
        previous = transaction

    results: list[AnomalyOut] = []
    for transaction in rows:
        previous_transaction = previous_by_id.get(transaction.id)
        context = context_from_transaction(transaction, previous_transaction)
        features = build_features(context, profile)
        reasons = explain(features, context, profile)
        results.append(
            AnomalyOut(
                transaction=TransactionOut.model_validate(transaction),
                anomaly_score=float(transaction.anomaly_score or 0.0),
                risk_score=float(transaction.risk_score or 0.0),
                reasons=reasons,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Alertes (fil temps réel) et journal d'audit
# ---------------------------------------------------------------------------
@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    limit: int = Query(default=30, ge=1, le=200),
    unread_only: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AlertOut]:
    """Fil des alertes, les plus récentes d'abord (interrogé toutes les 4 s)."""
    statement = select(Alert).where(Alert.user_id == user.id)
    if unread_only:
        statement = statement.where(Alert.read_at.is_(None))
    rows = list(db.scalars(statement.order_by(Alert.created_at.desc()).limit(limit)).all())
    return [AlertOut.model_validate(row) for row in rows]


@router.get("/alerts/summary")
def alerts_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Compteur d'alertes non lues + horodatage (utilisé par le fil temps réel)."""
    latest = db.scalar(select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc()).limit(1))
    return {
        "unread": unread_alert_count(db, user.id),
        "latest_id": latest.id if latest else None,
        "server_time": now_utc().isoformat() + "Z",
        "checked_at": now_utc().isoformat() + "Z",
    }


@router.post("/alerts/{alert_id}/read")
def read_alert(
    alert_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Marque une alerte comme lue."""
    if not mark_alert_read(db, user.id, alert_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alerte introuvable.")
    return {"status": "ok"}


@router.post("/alerts/read-all")
def read_all_alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Marque toutes les alertes comme lues."""
    rows = list(db.scalars(select(Alert).where(Alert.user_id == user.id, Alert.read_at.is_(None))).all())
    for alert in rows:
        alert.read_at = now_utc()
    db.commit()
    return {"status": "ok", "updated": len(rows)}


@router.get("/audit")
def audit_trail(
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Journal d'audit de l'utilisateur (connexions, paiements, blocages)."""
    rows = list(
        db.scalars(
            select(AuditLog).where(AuditLog.user_id == user.id).order_by(AuditLog.created_at.desc()).limit(limit)
        ).all()
    )
    return {
        "entries": [
            {
                "id": row.id,
                "action": row.action,
                "status": row.status,
                "detail": row.detail,
                "ip": row.ip_address,
                "date_locale": to_local(row.created_at).isoformat(),
            }
            for row in rows
        ]
    }


# ---------------------------------------------------------------------------
# Recommandations
# ---------------------------------------------------------------------------
@router.get("/recommendations", response_model=list[RecommendationOut])
def recommendations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[RecommendationOut]:
    """Conseils personnalisés de sécurité, d'épargne et de budget."""
    profile = get_profile(db, user)
    rows = build_recommendations(db, user, profile)
    return [RecommendationOut(**row) for row in rows]


# ---------------------------------------------------------------------------
# Modèle d'IA
# ---------------------------------------------------------------------------
@router.get("/model-card", response_model=ModelCardOut)
def get_model_card(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ModelCardOut:
    """
    Fiche descriptive du modèle : variables, métriques de détection (AUC,
    précision/rappel) et évaluation du moteur de décision complet
    (taux de détection des fraudes vs friction infligée aux paiements légitimes).
    """
    from app.services.risk_engine import evaluate_decisions

    card = model_card(db)
    card["decision_metrics"] = evaluate_decisions(db)
    return ModelCardOut(**card)


@router.post("/retrain", response_model=RetrainOut)
def retrain_model(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RetrainOut:
    """
    Réentraîne l'Isolation Forest sur l'historique de référence.

    En production, cet entraînement serait planifié (par exemple chaque nuit).
    Ici il est déclenché à la demande pour montrer le cycle de vie du modèle.
    """
    report = train_detector(db)
    log_action(
        db,
        action="reentrainement_modele",
        user_id=user.id,
        status="ok" if report["status"] == "ok" else "echec",
        detail=report["message"],
        ip_address=client_ip(request),
    )
    return RetrainOut(**report)


# ---------------------------------------------------------------------------
# Appareils de confiance
# ---------------------------------------------------------------------------
@router.get("/devices")
def list_devices(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Appareils associés au compte (vérifiés ou non)."""
    rows = list(db.scalars(select(Device).where(Device.user_id == user.id).order_by(Device.last_seen.desc())).all())
    return {
        "devices": [
            {
                "id": row.id,
                "label": row.label,
                "platform": row.platform,
                "is_trusted": row.is_trusted,
                "fingerprint": row.fingerprint,
                "first_seen": to_local(row.first_seen).isoformat(),
                "last_seen": to_local(row.last_seen).isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/devices/{device_id}/trust")
def trust_device(
    device_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Marque un appareil comme vérifié (de confiance)."""
    device = db.scalar(select(Device).where(Device.id == device_id, Device.user_id == user.id))
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appareil introuvable.")
    device.is_trusted = True
    db.commit()
    log_action(db, "appareil_verifie", user.id, "ok", f"Appareil « {device.label} » vérifié.", client_ip(request))
    return {"status": "ok", "message": "Appareil vérifié."}


@router.delete("/devices/{device_id}")
def delete_device(
    device_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Supprime un appareil (révocation d'accès)."""
    device = db.scalar(select(Device).where(Device.id == device_id, Device.user_id == user.id))
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appareil introuvable.")
    label = device.label
    db.delete(device)
    db.commit()
    log_action(db, "appareil_supprime", user.id, "ok", f"Appareil « {label} » supprimé.", client_ip(request))
    return {"status": "ok", "message": "Appareil supprimé."}
