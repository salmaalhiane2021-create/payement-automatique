"""
Création des alertes de fraude et des entrées du journal d'audit.

Les alertes alimentent le fil de sécurité temps réel de l'interface
(l'application interroge `/api/security/alerts` toutes les 4 secondes).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import now_utc
from app.i18n import t
from app.models import Alert, AuditLog


def severity_from_score(score: float) -> str:
    """Traduit un score de risque (0-100) en niveau de gravité."""
    if score >= 80:
        return "critique"
    if score >= 60:
        return "eleve"
    if score >= 40:
        return "moyen"
    return "faible"


def create_alert(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    kind: str = "anomalie",
    severity: str = "moyen",
    transaction_id: int | None = None,
    details: dict | None = None,
    commit: bool = True,
) -> Alert:
    """Enregistre une alerte destinée à l'utilisateur."""
    alert = Alert(
        user_id=user_id,
        transaction_id=transaction_id,
        kind=kind,
        severity=severity,
        title=title[:140],
        message=message,
        details=details or {},
        created_at=now_utc(),
    )
    db.add(alert)
    if commit:
        db.commit()
        db.refresh(alert)
    return alert


def alert_for_blocked_payment(
    db: Session,
    user_id: int,
    payment: dict,
    assessment,
    commit: bool = True,
    lang: str = "fr",
) -> Alert:
    """Alerte créée lorsqu'un paiement est refusé par le moteur de risque."""
    reasons = " · ".join(factor["label"] for factor in assessment.factors[:3]) or t(
        "alert.blocked.fallback_reason", lang
    )
    amount = f"{float(payment.get('amount', 0)):.2f} €"
    return create_alert(
        db,
        user_id=user_id,
        kind="fraude",
        severity=severity_from_score(assessment.score),
        title=t(
            "alert.blocked.title",
            lang,
            merchant=payment.get("merchant"),
            amount=amount,
        ),
        message=t(
            "alert.blocked.message",
            lang,
            amount=amount,
            merchant=payment.get("merchant"),
            reasons=reasons,
        ),
        details={
            "score_risque": assessment.score,
            "score_ia": assessment.anomaly_score,
            "facteurs": assessment.factors,
            "ville": payment.get("city"),
            "pays": payment.get("country"),
            "canal": payment.get("channel"),
        },
        commit=commit,
    )


def alert_for_high_anomaly(
    db: Session,
    user_id: int,
    transaction,
    anomaly_score: float,
    reasons: list[str],
    commit: bool = True,
    lang: str = "fr",
) -> Alert:
    """Alerte créée lorsqu'une opération exécutée reste très atypique."""
    # Les raisons issues du modèle d'IA (Isolation Forest) sont déjà résumées
    # par le message : on ne garde ici que les motifs lisibles par l'utilisateur.
    rule_reasons = [reason for reason in reasons if not reason.startswith("Comportement atypique détecté")]
    suffix = " ".join(rule_reasons[:2]) + " " if rule_reasons else ""
    amount = f"{abs(float(transaction.amount)):.2f} €"
    return create_alert(
        db,
        user_id=user_id,
        kind="anomalie",
        severity="eleve" if anomaly_score >= 0.75 else "moyen",
        title=t(
            "alert.anomaly.title",
            lang,
            merchant=transaction.merchant,
            amount=amount,
        ),
        message=t("alert.anomaly.message", lang, reasons=suffix),
        transaction_id=transaction.id,
        details={"score_ia": round(anomaly_score, 3), "raisons": reasons},
        commit=commit,
    )


def alert_security_tip(db: Session, user_id: int, title: str, message: str, severity: str = "faible") -> Alert:
    """Alerte de sensibilisation (sécurité du compte)."""
    return create_alert(db, user_id=user_id, kind="securite", severity=severity, title=title, message=message)


def unread_alert_count(db: Session, user_id: int) -> int:
    """Nombre d'alertes non lues."""
    return len(db.scalars(select(Alert.id).where(Alert.user_id == user_id, Alert.read_at.is_(None))).all())


def mark_alert_read(db: Session, user_id: int, alert_id: int) -> bool:
    """Marque une alerte comme lue ; False si elle n'appartient pas à l'utilisateur."""
    alert = db.scalar(select(Alert).where(Alert.id == alert_id, Alert.user_id == user_id))
    if alert is None:
        return False
    if alert.read_at is None:
        alert.read_at = now_utc()
        db.commit()
    return True


# ---------------------------------------------------------------------------
# Journal d'audit
# ---------------------------------------------------------------------------
def log_action(
    db: Session,
    action: str,
    user_id: int | None = None,
    status: str = "ok",
    detail: str = "",
    ip_address: str = "",
    user_agent: str = "",
    commit: bool = True,
) -> AuditLog:
    """
    Journalise un événement de sécurité (connexion, paiement, blocage…).

    Ce journal ne contient aucune donnée sensible : ni mot de passe, ni code OTP.
    """
    entry = AuditLog(
        user_id=user_id,
        action=action[:60],
        status=status,
        detail=detail[:1000],
        ip_address=ip_address[:45],
        user_agent=user_agent[:200],
        created_at=now_utc(),
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
