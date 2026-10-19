"""
Profil comportemental de l'utilisateur.

Principe
--------
À partir de l'historique, on construit une « carte d'identité financière » :
montants habituels, rythme horaire, jour de la semaine, villes, appareils,
canaux, abonnements… Ce profil est utilisé à deux endroits :

1. par le **moteur de risque**, pour mesurer l'écart d'une nouvelle opération
   par rapport aux habitudes ;
2. par le **tableau de bord « Sécurité & IA »**, pour expliquer à l'utilisateur
   ses propres habitudes.

Les transactions étiquetées comme frauduleuses (`is_fraud=True`) sont exclues :
un système réel ne doit pas apprendre les habitudes d'un fraudeur.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import now_utc
from app.models import BehavioralProfile, Transaction, User

TZ = ZoneInfo(settings.app_timezone)

# Nombre de mois d'historique conservés dans les statistiques mensuelles
MONTHS_WINDOW = 6
# Une paire ville/équipement est considérée « connue » à partir de 1 occurrence
NIGHT_HOURS = {23, 0, 1, 2, 3, 4, 5}


# ---------------------------------------------------------------------------
# Utilitaires de date
# ---------------------------------------------------------------------------
def to_local(dt: datetime | None, timezone_name: str | None = None) -> datetime:
    """
    Convertit un horodatage UTC naïf vers le fuseau local demandé.

    Sans argument, le fuseau de référence de l'application est utilisé
    (`APP_TIMEZONE`). Les interfaces passent le fuseau de l'utilisateur pour
    un affichage adapté à son pays.
    """
    zone = ZoneInfo(timezone_name) if timezone_name else TZ
    if dt is None:
        return now_utc().replace(tzinfo=timezone.utc).astimezone(zone)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(zone)


def iso(dt: datetime | None) -> str | None:
    """Sérialise une date en ISO 8601 (UTC) pour les réponses JSON."""
    return dt.isoformat() if dt else None


def percentile(sorted_values: list[float], p: float) -> float:
    """Centile par interpolation linéaire (évite une dépendance à numpy)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    index = (len(sorted_values) - 1) * (p / 100.0)
    low = int(index)
    high = min(low + 1, len(sorted_values) - 1)
    frac = index - low
    return float(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac)


def median_absolute_deviation(values: list[float]) -> float:
    """Écart absolu médian : mesure de dispersion résistante aux valeurs extrêmes."""
    if not values:
        return 0.0
    med = statistics.median(values)
    return float(statistics.median([abs(v - med) for v in values]))


# ---------------------------------------------------------------------------
# Statistiques élémentaires réutilisables
# ---------------------------------------------------------------------------
def category_breakdown(transactions: list[Transaction], limit: int | None = None) -> list[dict]:
    """Total et part des dépenses par catégorie (débits uniquement)."""
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for txn in transactions:
        if txn.amount >= 0:
            continue
        totals[txn.category] += abs(txn.amount)
        counts[txn.category] += 1

    grand_total = sum(totals.values()) or 1.0
    rows = [
        {
            "category": cat,
            "total": round(total, 2),
            "count": counts[cat],
            "share": round(total / grand_total, 4),
        }
        for cat, total in totals.items()
    ]
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows[:limit] if limit else rows


def daily_series(
    transactions: list[Transaction], days: int = 7, timezone_name: str | None = None
) -> list[dict]:
    """Séries quotidiennes débits / crédits sur les `days` derniers jours."""
    today = to_local(now_utc(), timezone_name).date()
    buckets = {(today - timedelta(days=offset)).isoformat(): {"debit": 0.0, "credit": 0.0} for offset in range(days)}

    for txn in transactions:
        key = to_local(txn.ts, timezone_name).date().isoformat()
        if key in buckets:
            if txn.amount < 0:
                buckets[key]["debit"] += abs(txn.amount)
            else:
                buckets[key]["credit"] += txn.amount

    return [
        {"date": key, "debit": round(value["debit"], 2), "credit": round(value["credit"], 2)}
        for key, value in sorted(buckets.items())
    ]


def monthly_totals(
    transactions: list[Transaction], months: int = MONTHS_WINDOW, timezone_name: str | None = None
) -> list[dict]:
    """Dépenses / revenus par mois calendaire."""
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"spending": 0.0, "income": 0.0})
    for txn in transactions:
        key = to_local(txn.ts, timezone_name).strftime("%Y-%m")
        if txn.amount < 0:
            buckets[key]["spending"] += abs(txn.amount)
        else:
            buckets[key]["income"] += txn.amount

    keys = sorted(buckets.keys())[-months:]
    return [
        {"month": key, "spending": round(buckets[key]["spending"], 2), "income": round(buckets[key]["income"], 2)}
        for key in keys
    ]


# ---------------------------------------------------------------------------
# Détection des abonnements
# ---------------------------------------------------------------------------
# Paiements réguliers qui ne sont pas des abonnements : le loyer et les
# retraits d'espèces sont mensuels mais ne se résilient pas.
NON_SUBSCRIPTION_MERCHANTS = {"Loyer", "Retrait DAB", "Virement salaire ACME"}


def detect_subscriptions(transactions: list[Transaction], timezone_name: str | None = None) -> list[dict]:
    """
    Repère les prélèvements récurrents.

    Critères : même marchand, au moins 3 occurrences, montant stable
    (écart-type < 15 % de la moyenne) et au moins 3 mois distincts.
    """
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for txn in transactions:
        if txn.amount < 0 and txn.merchant not in NON_SUBSCRIPTION_MERCHANTS:
            groups[txn.merchant].append(txn)

    subscriptions: list[dict] = []
    for merchant, items in groups.items():
        if len(items) < 3:
            continue
        amounts = [abs(item.amount) for item in items]
        mean_amount = statistics.fmean(amounts)
        if mean_amount <= 0:
            continue
        spread = statistics.pstdev(amounts) / mean_amount
        months = {to_local(item.ts, timezone_name).strftime("%Y-%m") for item in items}
        if spread < 0.15 and len(months) >= 3:
            most_recent = max(items, key=lambda item: item.ts)
            subscriptions.append(
                {
                    "merchant": merchant,
                    "amount": round(mean_amount, 2),
                    "category": most_recent.category,
                    "occurrences": len(items),
                    "months": len(months),
                    "last_seen": iso(most_recent.ts),
                }
            )

    subscriptions.sort(key=lambda row: row["amount"], reverse=True)
    return subscriptions


# ---------------------------------------------------------------------------
# Construction du profil
# ---------------------------------------------------------------------------
def _empty_profile(user: User) -> dict:
    """Profil neutre utilisé pour un compte sans historique (démarrage à froid)."""
    return {
        "generated_at": iso(now_utc()),
        "count": 0,
        "cold_start": True,
        "period_days": 0,
        "amount_stats": {
            "mean": 0.0,
            "median": 0.0,
            "std": 0.0,
            "mad": 0.0,
            "p10": 0.0,
            "p90": 0.0,
            "max": 0.0,
        },
        "hourly_hist": [1.0 / 24] * 24,
        "weekday_hist": [1.0 / 7] * 7,
        "known_devices": [],
        "known_cities": [],
        "known_countries": [user.country] if user.country else [],
        "known_channels": [],
        "known_merchants": [],
        "top_categories": [],
        "monthly": [],
        "average_monthly_spending": 0.0,
        "txn_per_day": 0.0,
        "night_ratio": 0.0,
        "online_ratio": 0.0,
        "subscriptions": [],
        "typical_hours": [],
        "insights": [],
    }


def build_profile(db: Session, user: User, include_frauds: bool = False) -> dict:
    """
    Calcule le profil comportemental complet d'un utilisateur.

    `include_frauds=False` (défaut) : les opérations étiquetées fraude sont
    écartées pour ne pas fausser les habitudes.
    """
    statement = select(Transaction).where(
        Transaction.user_id == user.id,
        Transaction.status != "blocked",  # une opération refusée n'a pas eu lieu
    )
    if not include_frauds:
        statement = statement.where(Transaction.is_fraud.is_(False))
    transactions = list(db.scalars(statement.order_by(Transaction.ts.asc())).all())

    if not transactions:
        return _empty_profile(user)

    debits = [abs(txn.amount) for txn in transactions if txn.amount < 0]
    if not debits:
        debits = [abs(txn.amount) for txn in transactions]

    debits_sorted = sorted(debits)
    # Toutes les statistiques horaires et calendaires sont calculées dans le
    # fuseau de l'utilisateur : ses habitudes sont locales, pas serveur.
    local_dates = [to_local(txn.ts, user.timezone) for txn in transactions]

    # --- Distribution horaire et hebdomadaire ---------------------------
    hour_counts = Counter(date.hour for date in local_dates)
    weekday_counts = Counter(date.weekday() for date in local_dates)
    total = len(transactions)
    hourly_hist = [hour_counts.get(hour, 0) / total for hour in range(24)]
    weekday_hist = [weekday_counts.get(day, 0) / total for day in range(7)]

    # --- Villes, appareils, canaux, marchands ---------------------------
    city_counts = Counter(txn.city for txn in transactions if txn.city)
    country_counts = Counter({txn.country for txn in transactions if txn.country})
    device_counts: Counter[str] = Counter(txn.device_fingerprint for txn in transactions if txn.device_fingerprint)
    channel_counts = Counter(txn.channel for txn in transactions if txn.channel)

    merchant_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "total": 0.0})
    for txn in transactions:
        if txn.amount < 0 and txn.merchant:
            merchant_stats[txn.merchant]["count"] += 1
            merchant_stats[txn.merchant]["total"] += abs(txn.amount)

    top_merchants = sorted(
        (
            {"merchant": name, "count": data["count"], "total": round(data["total"], 2)}
            for name, data in merchant_stats.items()
        ),
        key=lambda row: row["total"],
        reverse=True,
    )[:8]

    period_days = max(1, (local_dates[-1].date() - local_dates[0].date()).days + 1)
    monthly = monthly_totals(transactions, timezone_name=user.timezone)
    spending_months = [row["spending"] for row in monthly if row["spending"] > 0]
    average_monthly_spending = statistics.fmean(spending_months) if spending_months else 0.0

    profile = {
        "generated_at": iso(now_utc()),
        "count": total,
        "cold_start": False,
        "period_days": period_days,
        "amount_stats": {
            "mean": round(statistics.fmean(debits), 2),
            "median": round(statistics.median(debits), 2),
            "std": round(statistics.pstdev(debits) if len(debits) > 1 else 0.0, 2),
            "mad": round(median_absolute_deviation(debits), 2),
            "p10": round(percentile(debits_sorted, 10), 2),
            "p90": round(percentile(debits_sorted, 90), 2),
            "max": round(max(debits), 2),
        },
        "hourly_hist": [round(value, 5) for value in hourly_hist],
        "weekday_hist": [round(value, 5) for value in weekday_hist],
        "known_devices": [fp for fp, _ in device_counts.most_common(6)],
        "known_cities": [city for city, _ in city_counts.most_common(12)],
        "known_countries": sorted(country_counts) or ([user.country] if user.country else []),
        "known_channels": sorted(channel_counts),
        "known_merchants": top_merchants,
        "top_categories": category_breakdown(transactions, limit=8),
        "monthly": monthly,
        "average_monthly_spending": round(average_monthly_spending, 2),
        "txn_per_day": round(total / period_days, 2),
        "night_ratio": round(sum(1 for date in local_dates if date.hour in NIGHT_HOURS) / total, 4),
        "online_ratio": round(
            sum(1 for txn in transactions if txn.channel in {"en_ligne", "carte"}) / total,
            4,
        ),
        "subscriptions": detect_subscriptions(transactions),
        "typical_hours": [hour for hour, _ in hour_counts.most_common(3)],
        "main_city": city_counts.most_common(1)[0][0] if city_counts else user.city,
        "main_city_share": round(city_counts.most_common(1)[0][1] / total, 4) if city_counts else 0.0,
        "weekend_ratio": round(sum(1 for date in local_dates if date.weekday() >= 5) / total, 4),
    }
    profile["insights"] = _build_insights(profile, user)
    return profile


def _build_insights(profile: dict, user: User) -> list[str]:
    """Phrases d'observation affichées dans l'interface (en français)."""
    insights: list[str] = []
    stats = profile["amount_stats"]
    insights.append(
        f"Votre panier moyen est de {stats['mean']:.2f} € "
        f"(médiane {stats['median']:.2f} €, 9 opérations sur 10 sous {stats['p90']:.2f} €)."
    )

    hours = profile["typical_hours"]
    if hours:
        formated = ", ".join(f"{hour}h" for hour in sorted(hours))
        insights.append(f"Vos paiements se concentrent autour de {formated}.")

    if profile["night_ratio"] > 0.12:
        insights.append(
            f"{profile['night_ratio'] * 100:.1f} % de vos opérations ont lieu la nuit (23h-6h) : "
            "une habitude à surveiller."
        )
    if profile["weekend_ratio"] > 0.35:
        insights.append(f"{profile['weekend_ratio'] * 100:.1f} % de vos opérations sont réalisées le week-end.")

    if profile["main_city"]:
        insights.append(
            f"Ville principale : {profile['main_city']} "
            f"({profile['main_city_share'] * 100:.0f} % de vos opérations)."
        )
    if profile["online_ratio"] > 0.4:
        insights.append(f"{profile['online_ratio'] * 100:.0f} % de vos règlements se font à distance.")

    subscriptions = profile["subscriptions"]
    if subscriptions:
        total_subscriptions = sum(row["amount"] for row in subscriptions)
        insights.append(
            f"{len(subscriptions)} abonnements récurrents détectés, soit {total_subscriptions:.2f} €/mois."
        )

    if profile["top_categories"]:
        top = profile["top_categories"][0]
        insights.append(f"Poste de dépense principal : {top['category']} ({top['total']:.2f} €).")

    if profile["average_monthly_spending"] and user.monthly_income:
        rate = profile["average_monthly_spending"] / user.monthly_income
        insights.append(
            f"Vous dépensez en moyenne {rate * 100:.0f} % de vos revenus déclarés "
            f"({user.monthly_income:.0f} €/mois)."
        )
    return insights


# ---------------------------------------------------------------------------
# Persistance et cache
# ---------------------------------------------------------------------------
def refresh_profile(db: Session, user: User, commit: bool = True) -> dict:
    """
    Recalcule le profil et l'enregistre (appelé après chaque transaction).

    `commit=False` permet d'intégrer la mise à jour du profil à une
    transaction plus large (paiement atomique) : voir `payment_flow.confirm`.
    """
    profile = build_profile(db, user)
    row = db.scalar(select(BehavioralProfile).where(BehavioralProfile.user_id == user.id))
    if row is None:
        row = BehavioralProfile(user_id=user.id, data=profile, updated_at=now_utc())
        db.add(row)
    else:
        row.data = profile
        row.updated_at = now_utc()
    db.flush()
    if commit:
        db.commit()
    return profile


def get_profile(db: Session, user: User, max_age_seconds: int = 300) -> dict:
    """
    Renvoie le profil en cache s'il est récent, sinon le recalcule.

    Le cache évite de recalculer l'historique complet à chaque requête tout en
    garantissant des statistiques à jour (300 s max, ou après chaque paiement).
    """
    row = db.scalar(select(BehavioralProfile).where(BehavioralProfile.user_id == user.id))
    if row is not None:
        age = (now_utc() - row.updated_at).total_seconds()
        if age <= max_age_seconds and row.data:
            return row.data
    return refresh_profile(db, user)
