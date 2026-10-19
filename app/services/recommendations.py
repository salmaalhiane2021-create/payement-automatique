"""
Recommandations personnalisées : sécurité, épargne et maîtrise du budget.

Chaque conseil est déduit du profil comportemental réel de l'utilisateur
(il n'y a pas de texte générique recopié pour tout le monde) et comporte,
quand c'est possible, un **impact chiffré en euros par mois**.

Les règles sont volontairement transparentes : un conseiller ou un auditeur
peut relire la logique et vérifier chaque recommandation.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import now_utc
from app.models import Account, Alert, Device, Transaction, User

# Objectifs du budget 50/30/20 (besoins / envies / épargne)
SAVINGS_TARGET_RATIO = 0.20
# Seuil à partir duquel la part d'une catégorie devient préoccupante
CATEGORY_ALERT_SHARE = 0.30


def _rule_savings_capacity(user: User, profile: dict) -> dict | None:
    """Peut-il épargner davantage ? (revenus - dépenses moyennes)."""
    income = float(user.monthly_income or 0.0)
    spending = float(profile.get("average_monthly_spending") or 0.0)
    if income <= 0 or spending <= 0:
        return None

    capacity = income - spending
    if capacity <= 0:
        return {
            "code": "budget_depasse",
            "kind": "budget",
            "title": "Vos dépenses dépassent vos revenus",
            "detail": (
                f"En moyenne, vous dépensez {spending:.0f} € par mois pour {income:.0f} € de revenus, "
                f"soit un déficit de {abs(capacity):.0f} €. Réduisez d'abord les postes non essentiels."
            ),
            "priority": 1,
            "impact_eur": round(abs(capacity), 2),
            "action": "Voir le détail par catégorie",
        }

    rate = capacity / income
    if rate < SAVINGS_TARGET_RATIO:
        target = income * SAVINGS_TARGET_RATIO
        return {
            "code": "epargne_insuffisante",
            "kind": "epargne",
            "title": f"Objectif d'épargne : {target:.0f} € par mois",
            "detail": (
                f"Vous épargnez actuellement environ {capacity:.0f} € ({rate * 100:.0f} % de vos revenus). "
                f"La règle du 50/30/20 recommande 20 %, soit {target:.0f} €. Il manque {target - capacity:.0f} € par mois."
            ),
            "priority": 2,
            "impact_eur": round(target - capacity, 2),
            "action": "Programmer un virement automatique",
        }

    return {
        "code": "epargne_solide",
        "kind": "epargne",
        "title": f"Bonne dynamique : {capacity:.0f} € épargnables par mois",
        "detail": (
            f"Vous conservez {rate * 100:.0f} % de vos revenus. Placez {capacity * 0.6:.0f} € sur un support "
            "rémunéré et gardez le reste en épargne de précaution."
        ),
        "priority": 5,
        "impact_eur": round(capacity * 0.6, 2),
        "action": "Répartir mon épargne",
    }


def _rule_emergency_fund(db: Session, user: User, profile: dict) -> dict | None:
    """Fonds d'urgence : au moins un mois de dépenses disponibles."""
    spending = float(profile.get("average_monthly_spending") or 0.0)
    if spending <= 0:
        return None

    savings = db.scalar(
        select(func.coalesce(func.sum(Account.balance), 0.0)).where(Account.user_id == user.id, Account.kind == "epargne")
    )
    savings = float(savings or 0.0)
    target = spending  # un mois de dépenses

    if savings >= target:
        return None
    return {
        "code": "fonds_urgence",
        "kind": "epargne",
        "title": f"Fonds d'urgence à compléter : {target - savings:.0f} €",
        "detail": (
            f"Votre épargne disponible ({savings:.0f} €) couvre moins d'un mois de dépenses ({spending:.0f} €). "
            "Objectif conseillé : 3 mois, en commençant par 1 mois."
        ),
        "priority": 2,
        "impact_eur": round(target - savings, 2),
        "action": "Alimenter mon épargne",
    }


def _rule_subscriptions(profile: dict) -> dict | None:
    """Abonnements récurrents détectés : à vérifier régulièrement."""
    subscriptions = profile.get("subscriptions") or []
    if not subscriptions:
        return None

    total = sum(row["amount"] for row in subscriptions)
    if total < 30:
        return None

    names = ", ".join(row["merchant"] for row in subscriptions[:4])
    return {
        "code": "abonnements",
        "kind": "budget",
        "title": f"{len(subscriptions)} abonnements pour {total:.0f} €/mois",
        "detail": (
            f"Soit {total * 12:.0f} € par an. Détectés : {names}. "
            "Résiliez ceux qui ne sont plus utilisés : c'est l'économie la plus rapide."
        ),
        "priority": 3,
        "impact_eur": round(total * 0.25, 2),
        "action": "Revoir mes abonnements",
    }


def _rule_top_category(profile: dict) -> dict | None:
    """Concentration des dépenses sur une seule catégorie."""
    categories = profile.get("top_categories") or []
    if not categories:
        return None

    top = categories[0]
    if top.get("share", 0) < CATEGORY_ALERT_SHARE or top.get("total", 0) < 100:
        return None

    saving = top["total"] * 0.10
    return {
        "code": "categorie_dominante",
        "kind": "budget",
        "title": f"{top['category']} représente {top['share'] * 100:.0f} % de vos dépenses",
        "detail": (
            f"Ce poste totalise {top['total']:.0f} € sur la période. Une réduction de 10 % "
            f"libérerait environ {saving:.0f} € par mois."
        ),
        "priority": 3,
        "impact_eur": round(saving, 2),
        "action": "Fixer un plafond mensuel",
    }


def _rule_night_activity(profile: dict) -> dict | None:
    """Activité nocturne inhabituelle : risque de fraude plus élevé."""
    night_ratio = float(profile.get("night_ratio") or 0.0)
    if night_ratio < 0.15:
        return None
    return {
        "code": "activite_nocturne",
        "kind": "securite",
        "title": "Surveillez vos paiements nocturnes",
        "detail": (
            f"{night_ratio * 100:.0f} % de vos opérations ont lieu entre 23h et 6h, période où les fraudes "
            "sont les plus fréquentes. Activez les notifications instantanées."
        ),
        "priority": 2,
        "impact_eur": None,
        "action": "Activer les notifications",
    }


def _rule_pin_missing(user: User) -> dict | None:
    """Aucun code PIN : la vérification renforcée est impossible."""
    if user.pin_hash:
        return None
    return {
        "code": "pin_absent",
        "kind": "securite",
        "title": "Définissez un code PIN de paiement",
        "detail": (
            "Sans code PIN, les opérations à risque élevé ne peuvent pas être validées par un second facteur "
            "et sont donc bloquées."
        ),
        "priority": 1,
        "impact_eur": None,
        "action": "Créer mon code PIN",
    }


def _rule_devices(db: Session, user: User) -> dict | None:
    """Contrôle des appareils de confiance enregistrés."""
    total_devices = int(db.scalar(select(func.count(Device.id)).where(Device.user_id == user.id)) or 0)
    if total_devices == 0:
        return {
            "code": "aucun_appareil",
            "kind": "securite",
            "title": "Enregistrez un appareil de confiance",
            "detail": (
                "Aucun appareil n'est associé à votre compte : chaque paiement est alors considéré comme "
                "provenant d'un appareil inconnu, ce qui fait grimper le score de risque."
            ),
            "priority": 2,
            "impact_eur": None,
            "action": "Gérer mes appareils",
        }

    untrusted = int(
        db.scalar(
            select(func.count(Device.id)).where(Device.user_id == user.id, Device.is_trusted.is_(False))
        )
        or 0
    )
    if untrusted:
        return {
            "code": "appareil_non_fiable",
            "kind": "securite",
            "title": f"{untrusted} appareil(s) non vérifié(s)",
            "detail": (
                "Vérifiez ces appareils ou supprimez-les : un appareil compromis permet de contourner "
                "votre authentification."
            ),
            "priority": 1,
            "impact_eur": None,
            "action": "Vérifier mes appareils",
        }
    return None


def _rule_blocked_recently(db: Session, user: User) -> dict | None:
    """Rappel si des opérations ont été bloquées récemment."""
    since = now_utc() - timedelta(days=30)
    blocked = int(
        db.scalar(
            select(func.count(Transaction.id)).where(
                Transaction.user_id == user.id,
                Transaction.status == "blocked",
                Transaction.ts >= since,
            )
        )
        or 0
    )
    if blocked == 0:
        return None
    return {
        "code": "blocages_recents",
        "kind": "securite",
        "title": f"{blocked} opération(s) bloquée(s) ces 30 derniers jours",
        "detail": (
            "Des paiements ont été refusés par le moteur de risque. Consultez le détail : si ces opérations "
            "ne sont pas les vôtres, changez immédiatement votre mot de passe et votre code PIN."
        ),
        "priority": 1,
        "impact_eur": None,
        "action": "Consulter les alertes",
    }


def _rule_unread_alerts(db: Session, user: User) -> dict | None:
    """Alertes non lues."""
    unread = int(
        db.scalar(select(func.count(Alert.id)).where(Alert.user_id == user.id, Alert.read_at.is_(None))) or 0
    )
    if unread == 0:
        return None
    return {
        "code": "alertes_non_lues",
        "kind": "securite",
        "title": f"{unread} alerte(s) de sécurité à consulter",
        "detail": "Chaque alerte contient le détail des signaux détectés et l'action recommandée.",
        "priority": 2,
        "impact_eur": None,
        "action": "Ouvrir le centre de sécurité",
    }


def build_recommendations(db: Session, user: User, profile: dict | None = None) -> list[dict]:
    """
    Construit la liste des recommandations, triée par priorité puis par impact.

    Retourne une liste de dictionnaires prêts à être sérialisés par l'API.
    """
    profile = profile or {}
    candidates = [
        _rule_pin_missing(user),
        _rule_devices(db, user),
        _rule_blocked_recently(db, user),
        _rule_unread_alerts(db, user),
        _rule_savings_capacity(user, profile),
        _rule_emergency_fund(db, user, profile),
        _rule_subscriptions(profile),
        _rule_top_category(profile),
        _rule_night_activity(profile),
    ]

    recommendations = [item for item in candidates if item]
    recommendations.sort(key=lambda item: (item["priority"], -(item["impact_eur"] or 0.0)))
    return recommendations
