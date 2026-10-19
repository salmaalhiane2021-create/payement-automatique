"""
Ingénierie des variables (feature engineering) pour la détection d'anomalies.

Chaque transaction est décrite par 10 variables numériques comparant
l'opération aux habitudes de l'utilisateur. Ces variables sont :

* lisibles : chacune a un nom et un libellé français (utilisés pour expliquer
  la décision à l'utilisateur) ;
* normalisées en amont du modèle (StandardScaler), donc sans unité ;
* calculables aussi bien à l'entraînement qu'en production (pas de décalage
  entre les deux).

    Variable             Signification
    ------------------   ----------------------------------------------------
    amount_log           log10 du montant (compresse les très gros montants)
    amount_zscore        écart au montant médian, en écarts absolus médians
    amount_vs_p90        montant / 90e centile historique de l'utilisateur
    hour_unusual         0 = heure habituelle, 1 = heure jamais observée
                         (lissé avec les heures voisines, plafonné de jour)
    new_device           1 si l'appareil n'a jamais servi
    new_location         1 si la ville n'a jamais été vue
    txn_count_1h         nombre d'opérations dans l'heure précédente
    geo_velocity         vitesse de déplacement implicite (km/h)
    night_time           1 si l'opération a lieu entre 23h et 6h
    new_channel          1 si le canal de paiement est nouveau
"""

from __future__ import annotations

import math
from datetime import datetime

from app.services import geo
from app.services.behavior import NIGHT_HOURS, to_local

# Version du schéma de variables. À incrémenter dès qu'une variable est
# ajoutée, renommée ou change de sens : les artefacts entraînés avec une autre
# version sont refusés au chargement (réentraînement nécessaire).
FEATURE_VERSION = 2

FEATURE_NAMES: list[str] = [
    "amount_log",
    "amount_zscore",
    "amount_vs_p90",
    "hour_unusual",
    "new_device",
    "new_location",
    "txn_count_1h",
    "geo_velocity",
    "night_time",
    "new_channel",
]

FEATURE_LABELS_FR: dict[str, str] = {
    "amount_log": "Montant de l'opération",
    "amount_zscore": "Écart au montant habituel",
    "amount_vs_p90": "Montant comparé à vos 10 % d'opérations les plus élevées",
    "hour_unusual": "Heure d'achat inhabituelle",
    "new_device": "Appareil non reconnu",
    "new_location": "Lieu non habituel",
    "txn_count_1h": "Rythme d'opérations dans l'heure",
    "geo_velocity": "Déplacement géographique (vitesse implicite)",
    "night_time": "Opération pendant la nuit",
    "new_channel": "Canal de paiement inhabituel",
}

# Valeurs de repli pour un compte sans historique (démarrage à froid)
COLD_START_AMOUNT_MEDIAN = 45.0
COLD_START_AMOUNT_MAD = 30.0

# Bornes de sécurité (évitent les valeurs extrêmes qui écraseraient le modèle)
MAX_ZSCORE = 40.0
MAX_VELOCITY = 2000.0
MAX_TXN_COUNT = 25


def hour_unusual_rate(profile: dict, hour: int) -> float:
    """
    Mesure l'atypisme d'une heure d'achat (0 = banal, 1 = très atypique).

    Le calcul part de la fréquence observée à cette heure, puis l'atténue :

    * **lissage** : une heure encadrée par de l'activité (achat à 15h pour un
      profil qui paie à 14h et 16h) n'est pas considérée comme « jamais vue » ;
    * **plafond de jour** : hors 23h-6h, l'atypisme est plafonné à 0.5 : une
      heure inhabituelle en journée est un signal faible, pas une alerte.

    Résultat : seule une activité réellement inattendue (nuit, heure isolée
    sans voisine active) atteint 1.0.
    """
    histogram = profile.get("hourly_hist") or []
    if not histogram or len(histogram) != 24:
        return 0.0
    observed = profile.get("count", 0) >= 8  # en dessous : historique trop mince
    if not observed:
        return 0.0

    hour = max(0, min(23, hour))
    probability = histogram[hour]
    neighbours = histogram[hour - 1] if hour > 0 else 0.0
    neighbours += histogram[hour + 1] if hour < 23 else 0.0
    smoothed = max(probability, 0.35 * neighbours)

    rate = max(0.0, min(1.0, 1.0 - smoothed * 24.0))
    if hour not in NIGHT_HOURS:
        rate = min(rate, 0.5)
    return round(rate, 4)


def build_features(context: dict, profile: dict) -> dict[str, float]:
    """
    Construit le dictionnaire de variables pour une opération.

    `context` accepte les clés : amount, hour, device_fingerprint, city,
    channel, velocity_kmh, txn_count_1h.
    """
    amount = float(context.get("amount") or 0.0)
    hour = int(context.get("hour", 0))
    stats = profile.get("amount_stats") or {}
    cold_start = bool(profile.get("cold_start", True)) or profile.get("count", 0) == 0

    # --- Montant ---------------------------------------------------------
    if cold_start or not stats.get("median"):
        median = COLD_START_AMOUNT_MEDIAN
        spread = COLD_START_AMOUNT_MAD
        p90 = COLD_START_AMOUNT_MEDIAN * 3
    else:
        median = float(stats.get("median") or 0.0)
        # 1.4826 × MAD ≈ écart-type pour une loi normale (estimateur robuste)
        spread = max(1.4826 * float(stats.get("mad") or 0.0), 15.0)
        p90 = max(float(stats.get("p90") or median), 1.0)

    zscore = max(-10.0, min(MAX_ZSCORE, (amount - median) / spread))

    # --- Contexte géographique / matériel / canal ------------------------
    known_devices = set(profile.get("known_devices") or [])
    known_cities = set(profile.get("known_cities") or [])
    known_channels = set(profile.get("known_channels") or [])
    device = context.get("device_fingerprint") or ""
    city = (context.get("city") or "").strip()
    channel = (context.get("channel") or "").strip()

    # Démarrage à froid : tout est « nouveau », ce qui ne veut rien dire.
    new_device = 0.0 if cold_start else float(bool(device) and device not in known_devices)
    new_location = 0.0 if cold_start else float(bool(city) and city not in known_cities)
    new_channel = 0.0 if cold_start else float(bool(channel) and channel not in known_channels)

    return {
        "amount_log": math.log10(1.0 + max(0.0, amount)),
        "amount_zscore": round(zscore, 4),
        "amount_vs_p90": round(min(amount / p90, 25.0), 4),
        "hour_unusual": round(hour_unusual_rate(profile, hour), 4),
        "new_device": new_device,
        "new_location": new_location,
        "txn_count_1h": float(min(context.get("txn_count_1h") or 0, MAX_TXN_COUNT)),
        "geo_velocity": float(min(context.get("velocity_kmh") or 0.0, MAX_VELOCITY)),
        "night_time": float(hour in NIGHT_HOURS),
        "new_channel": new_channel,
    }


def to_vector(features: dict[str, float]) -> list[float]:
    """Convertit le dictionnaire de variables en vecteur ordonné pour le modèle."""
    return [float(features.get(name, 0.0)) for name in FEATURE_NAMES]


def context_from_transaction(
    transaction,
    previous=None,
    txn_count_1h: int = 0,
    timezone_name: str | None = None,
) -> dict:
    """
    Construit le contexte d'une transaction enregistrée (utilisé pour
    l'entraînement et pour le recalcul des scores de l'historique).

    `timezone_name` permet d'utiliser le fuseau de l'utilisateur propriétaire
    de l'historique (les habitudes horaires sont locales, pas serveur).
    """
    local_ts = to_local(transaction.ts, timezone_name)
    velocity = 0.0
    if previous is not None:
        velocity, _ = geo.travel_velocity_kmh(
            previous.latitude,
            previous.longitude,
            previous.ts,
            transaction.latitude,
            transaction.longitude,
            transaction.ts,
        )
    return {
        "amount": abs(transaction.amount),
        "hour": local_ts.hour,
        "device_fingerprint": transaction.device_fingerprint,
        "city": transaction.city,
        "channel": transaction.channel,
        "velocity_kmh": velocity,
        "txn_count_1h": txn_count_1h,
    }


def human_amount_ratio(amount: float, profile: dict) -> float | None:
    """Ratio montant / médiane historique (None si historique insuffisant)."""
    stats = profile.get("amount_stats") or {}
    median = float(stats.get("median") or 0.0)
    if median <= 0 or profile.get("cold_start"):
        return None
    return amount / median


def local_hour_of(ts: datetime, timezone_name: str | None = None) -> int:
    """Heure locale d'un horodatage UTC naïf (fuseau utilisateur facultatif)."""
    return to_local(ts, timezone_name).hour
