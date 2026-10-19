"""
Géolocalisation : coordonnées de villes, distance et vitesse de déplacement.

Ces données servent à détecter les **déplacements impossibles** : une même
carte utilisée à Paris puis à Lyon 6 minutes plus tard suppose une vitesse
supérieure à 4 000 km/h, ce qui est une signature classique d'usurpation.
"""

from __future__ import annotations

import math

# Ville -> (latitude, longitude, pays)
CITY_COORDS: dict[str, tuple[float, float, str]] = {
    # --- France ---------------------------------------------------------
    "Paris": (48.8566, 2.3522, "France"),
    "Lyon": (45.7640, 4.8357, "France"),
    "Marseille": (43.2965, 5.3698, "France"),
    "Toulouse": (43.6047, 1.4442, "France"),
    "Nice": (43.7102, 7.2620, "France"),
    "Nantes": (47.2184, -1.5536, "France"),
    "Montpellier": (43.6108, 3.8767, "France"),
    "Strasbourg": (48.5734, 7.7521, "France"),
    "Bordeaux": (44.8378, -0.5792, "France"),
    "Lille": (50.6292, 3.0573, "France"),
    "Rennes": (48.1173, -1.6778, "France"),
    "Reims": (49.2583, 4.0317, "France"),
    "Toulon": (43.1242, 5.9280, "France"),
    "Grenoble": (45.1885, 5.7245, "France"),
    "Dijon": (47.3220, 5.0415, "France"),
    "Angers": (47.4784, -0.5632, "France"),
    "Clermont-Ferrand": (45.7772, 3.0870, "France"),
    "Brest": (48.3904, -4.4861, "France"),
    "Limoges": (45.8336, 1.2611, "France"),
    "Tours": (47.3941, 0.6848, "France"),
    "Metz": (49.1193, 6.1757, "France"),
    "Rouen": (49.4432, 1.0999, "France"),
    "Annecy": (45.8992, 6.1294, "France"),
    "Perpignan": (42.6887, 2.8948, "France"),
    "Biarritz": (43.4832, -1.5586, "France"),
    # --- Europe ---------------------------------------------------------
    "Londres": (51.5074, -0.1278, "Royaume-Uni"),
    "Bruxelles": (50.8503, 4.3517, "Belgique"),
    "Genève": (46.2044, 6.1432, "Suisse"),
    "Barcelone": (41.3874, 2.1686, "Espagne"),
    "Madrid": (40.4168, -3.7038, "Espagne"),
    "Milan": (45.4642, 9.1900, "Italie"),
    "Berlin": (52.5200, 13.4050, "Allemagne"),
    "Amsterdam": (52.3676, 4.9041, "Pays-Bas"),
    "Lisbonne": (38.7223, -9.1393, "Portugal"),
    "Istanbul": (41.0082, 28.9784, "Turquie"),
    "Moscou": (55.7558, 37.6173, "Russie"),
    "Kiev": (50.4501, 30.5234, "Ukraine"),
    # --- Monde ----------------------------------------------------------
    "New York": (40.7128, -74.0060, "États-Unis"),
    "Montréal": (45.5019, -73.5674, "Canada"),
    "Dubaï": (25.2048, 55.2708, "Émirats arabes unis"),
    "Lagos": (6.5244, 3.3792, "Nigéria"),
    "Casablanca": (33.5731, -7.5898, "Maroc"),
    "Tunis": (36.8065, 10.1815, "Tunisie"),
    "Alger": (36.7538, 3.0588, "Algérie"),
    "Le Caire": (30.0444, 31.2357, "Égypte"),
    "Nairobi": (-1.2921, 36.8219, "Kenya"),
    "Johannesburg": (-26.2041, 28.0473, "Afrique du Sud"),
    "Pékin": (39.9042, 116.4074, "Chine"),
    "Hong Kong": (22.3193, 114.1694, "Chine"),
    "Singapour": (1.3521, 103.8198, "Singapour"),
    "Tokyo": (35.6762, 139.6503, "Japon"),
    "Bangkok": (13.7563, 100.5018, "Thaïlande"),
    "Sydney": (-33.8688, 151.2093, "Australie"),
    "São Paulo": (-23.5505, -46.6333, "Brésil"),
}

# Seuils utilisés par le moteur de risque
IMPOSSIBLE_SPEED_KMH = 900.0   # au-delà : déplacement physiquement impossible
SUSPICIOUS_SPEED_KMH = 300.0   # au-delà : déplacement très rapide (train/avion)
MIN_HOURS_FOR_VELOCITY = 0.05  # < 3 minutes : la vitesse n'est pas interprétable


def coords_for(city: str, country: str | None = None) -> tuple[float, float] | None:
    """Retourne (latitude, longitude) d'une ville connue, sinon None."""
    entry = CITY_COORDS.get((city or "").strip())
    if entry is None:
        return None
    return entry[0], entry[1]


def country_for(city: str, default: str = "France") -> str:
    """Pays associé à une ville connue."""
    entry = CITY_COORDS.get((city or "").strip())
    return entry[2] if entry else default


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique entre deux points, en kilomètres."""
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def travel_velocity_kmh(
    prev_lat: float | None,
    prev_lon: float | None,
    prev_ts,
    lat: float | None,
    lon: float | None,
    ts,
) -> tuple[float, float]:
    """
    Vitesse nécessaire pour passer de la position précédente à la nouvelle.

    Retourne (vitesse_km_h, distance_km). Une vitesse de 0 signifie « non
    calculable » (position absente ou transactions trop rapprochées).
    """
    if None in (prev_lat, prev_lon, lat, lon) or prev_ts is None or ts is None:
        return 0.0, 0.0

    distance = haversine_km(prev_lat, prev_lon, lat, lon)
    hours = abs((ts - prev_ts).total_seconds()) / 3600.0
    if hours < MIN_HOURS_FOR_VELOCITY or distance < 1.0:
        # Transactions quasi simultanées : la vitesse n'a pas de sens physique.
        return 0.0, distance
    return distance / hours, distance
