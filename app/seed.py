"""
Génération du jeu de données de démonstration.

Ce module produit un historique financier **réaliste mais entièrement fictif** :
utilisateurs français, salaires mensuels, loyers, abonnements, courses,
restaurants, transports… puis y injecte des **scénarios de fraude** cohérents
(montant anormal, appareil inconnu, déplacement impossible, heures
inhabituelles, rafale d'opérations, catégorie à risque).

Le générateur est **déterministe** (graine fixe) : deux exécutions produisent
exactement le même jeu de données, ce qui rend les démonstrations et les tests
reproductibles.

Usage :
    python -m app.seed            # crée les données si la base est vide
    python -m app.seed --force    # efface tout et régénère
"""

from __future__ import annotations

import argparse
import random
from datetime import timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, init_db, now_utc
from app.ml.anomaly import train_detector
from app.models import (
    Account,
    Alert,
    AuditLog,
    BehavioralProfile,
    Device,
    PaymentChallenge,
    Transaction,
    User,
)
from app.security import generate_device_fingerprint, hash_password, hash_pin
from app.services import alerts as alerts_service
from app.services import geo
from app.services.behavior import TZ, build_profile, refresh_profile, to_local
from app.services.risk_engine import assess_payment

SEED = 20241018  # graine fixe => jeu de données reproductible

# ---------------------------------------------------------------------------
# Identifiants de démonstration (affichés dans le README et la page de connexion)
# ---------------------------------------------------------------------------
DEMO_PASSWORD = "Demo1234!"
DEMO_PIN = "4321"

# ---------------------------------------------------------------------------
# Catalogue de commerçants : catégorie -> [(nom, montant_min, montant_max)]
# Les montants sont exprimés en euros pour un revenu de référence de 2 800 €.
# ---------------------------------------------------------------------------
MERCHANTS: dict[str, list[tuple[str, float, float]]] = {
    "Alimentation": [
        ("Carrefour Market", 18, 95),
        ("Monoprix", 8, 55),
        ("Franprix", 5, 35),
        ("Lidl", 12, 70),
        ("Biocoop", 15, 80),
        ("Boulangerie Maison Blanc", 2, 12),
    ],
    "Restaurants": [
        ("Le Petit Bistrot", 22, 75),
        ("Sushi Shop", 15, 45),
        ("Big Fernand", 14, 32),
        ("Chez Marcel", 20, 60),
        ("Starbucks", 4, 14),
        ("Pizza Roma", 11, 30),
    ],
    "Transport": [
        ("RATP", 2, 20),
        ("SNCF Connect", 15, 120),
        ("Uber", 8, 45),
        ("TotalEnergies Station", 40, 90),
        ("Blablacar", 12, 45),
        ("Vélib Métropole", 1, 12),
    ],
    "Shopping": [
        ("Amazon", 12, 180),
        ("Fnac", 20, 250),
        ("Zara", 25, 120),
        ("Decathlon", 18, 140),
        ("Sephora", 15, 80),
        ("IKEA", 30, 320),
    ],
    "Loisirs": [
        ("UGC Ciné Cité", 9, 26),
        ("Spotify", 10, 12),
        ("Steam", 5, 60),
        ("Fnac Spectacles", 25, 90),
        ("Netflix", 13, 20),
        ("Librairie Gallimard", 12, 45),
    ],
    "Santé": [
        ("Pharmacie du Centre", 6, 55),
        ("Doctolib", 25, 70),
        ("Cabinet Dentaire Saint-Michel", 35, 120),
        ("Optique Vision", 40, 260),
    ],
    "Énergie": [
        ("EDF", 45, 130),
        ("Engie", 40, 120),
        ("Veolia Eau", 20, 60),
    ],
    "Abonnements": [
        ("Orange Fibre", 29, 45),
        ("Free Mobile", 9, 22),
        ("Canal+", 22, 35),
        ("iCloud Apple", 2, 10),
    ],
}

# Répartition par défaut des dépenses (poids relatifs)
CATEGORY_WEIGHTS: dict[str, float] = {
    "Alimentation": 30,
    "Restaurants": 14,
    "Transport": 14,
    "Shopping": 10,
    "Loisirs": 8,
    "Santé": 4,
    "Énergie": 4,
    "Abonnements": 4,
    "Retrait": 5,
}

# Commerçants utilisés uniquement pour les scénarios de fraude
FRAUD_MERCHANTS = {
    "Crypto": ["CryptoExchange LTD", "BitFast Trade", "CoinNova"],
    "Jeux d'argent": ["iGaming Pro", "BetElite Casino", "PokerLive 24"],
    "Transfert international": ["Western Union", "MoneyGram Express", "TransferGo"],
    "Électronique": ["TechDeal Store", "PremiumGadgets Online"],
}

# ---------------------------------------------------------------------------
# Profils utilisateurs
# ---------------------------------------------------------------------------
USER_SPECS: list[dict] = [
    {
        "full_name": "Camille Bernard",
        "email": "demo@nexpay.fr",
        "city": "Paris",
        "country": "France",
        "income": 2800.0,
        "risk_aversion": 0.6,
        "chronotype": "jour",
        "months": 5,
        "primary": True,
        "phone": "0612345678",
        "devices": [("iPhone 15", "iOS 17"), ("MacBook Pro", "macOS 14")],
        "secondary_cities": ["Lyon"],
    },
    {
        "full_name": "Yassine El Amrani",
        "email": "yassine@nexpay.fr",
        "city": "Lyon",
        "country": "France",
        "income": 3450.0,
        "risk_aversion": 0.4,
        "chronotype": "soir",
        "months": 4,
        "phone": "0623456789",
        "devices": [("Pixel 8", "Android 14"), ("PC Bureau", "Windows 11")],
        "secondary_cities": ["Grenoble", "Marseille"],
    },
    {
        "full_name": "Léa Moreau",
        "email": "lea@nexpay.fr",
        "city": "Marseille",
        "country": "France",
        "income": 2150.0,
        "risk_aversion": 0.7,
        "chronotype": "jour",
        "months": 4,
        "phone": "0634567890",
        "devices": [("Galaxy S23", "Android 14")],
        "secondary_cities": ["Nice", "Toulon"],
    },
    {
        "full_name": "Thomas Dubois",
        "email": "thomas@nexpay.fr",
        "city": "Toulouse",
        "country": "France",
        "income": 4100.0,
        "risk_aversion": 0.3,
        "chronotype": "soir",
        "months": 5,
        "phone": "0645678901",
        "devices": [("iPhone 14 Pro", "iOS 17"), ("iPad Air", "iPadOS 17"), ("MacBook Air", "macOS 14")],
        "secondary_cities": ["Bordeaux", "Barcelone"],
    },
    {
        "full_name": "Aïcha Diallo",
        "email": "aicha@nexpay.fr",
        "city": "Lille",
        "country": "France",
        "income": 1950.0,
        "risk_aversion": 0.8,
        "chronotype": "nuit",
        "months": 4,
        "phone": "0656789012",
        "devices": [("Xiaomi 13", "Android 13")],
        "secondary_cities": ["Bruxelles"],
    },
    {
        "full_name": "Hugo Lefèvre",
        "email": "hugo@nexpay.fr",
        "city": "Bordeaux",
        "country": "France",
        "income": 2650.0,
        "risk_aversion": 0.5,
        "chronotype": "jour",
        "months": 4,
        "phone": "0667890123",
        "devices": [("iPhone 13", "iOS 16"), ("PC Portable", "Windows 11")],
        "secondary_cities": ["Biarritz"],
    },
    {
        "full_name": "Inès Rossi",
        "email": "ines@nexpay.fr",
        "city": "Nice",
        "country": "France",
        "income": 3100.0,
        "risk_aversion": 0.55,
        "chronotype": "soir",
        "months": 4,
        "phone": "0678901234",
        "devices": [("iPhone 12", "iOS 16")],
        "secondary_cities": ["Milan"],
    },
    {
        "full_name": "Karim Benali",
        "email": "karim@nexpay.fr",
        "city": "Nantes",
        "country": "France",
        "income": 2350.0,
        "risk_aversion": 0.65,
        "chronotype": "jour",
        "months": 4,
        "phone": "0689012345",
        "devices": [("OnePlus 11", "Android 14"), ("ThinkPad", "Linux")],
        "secondary_cities": ["Rennes"],
    },
]

# Horaire de référence selon le chronotype (poids par heure)
CHRONOTYPES: dict[str, dict[int, float]] = {
    "jour": {7: 2, 8: 5, 9: 4, 12: 9, 13: 8, 14: 3, 17: 4, 18: 6, 19: 5, 20: 3, 21: 1},
    "soir": {9: 2, 11: 3, 13: 5, 16: 3, 18: 6, 19: 8, 20: 7, 21: 5, 22: 3, 23: 1},
    "nuit": {1: 3, 2: 4, 3: 3, 12: 4, 14: 3, 18: 4, 20: 5, 22: 6, 23: 5},
}


# ---------------------------------------------------------------------------
# Utilitaires aléatoires
# ---------------------------------------------------------------------------
def weighted_choice(rng: random.Random, weights: dict) -> object:
    """Tirage aléatoire proportionnel aux poids."""
    population = list(weights.keys())
    return rng.choices(population, weights=[weights[key] for key in population], k=1)[0]


def sample_amount(rng: random.Random, low: float, high: float, scale: float = 1.0) -> float:
    """Montant réaliste : distribution triangulaire (pic bas, longue traîne)."""
    mode = low + (high - low) * 0.35
    return round(rng.triangular(low, high, mode) * scale, 2)


def pick_hour(rng: random.Random, chronotype: str) -> int:
    """Heure locale d'une opération selon le rythme de vie de l'utilisateur."""
    weights = dict(CHRONOTYPES.get(chronotype, CHRONOTYPES["jour"]))
    return int(weighted_choice(rng, {hour: weight for hour, weight in weights.items()}))


def channel_for_category(rng: random.Random, category: str) -> str:
    """Canal de paiement plausible pour une catégorie."""
    if category in {"Abonnements", "Énergie"}:
        return "prelevement"
    if category == "Retrait":
        return "atm"
    if category in {"Restaurants", "Alimentation"}:
        return rng.choices(["carte", "en_ligne"], weights=[85, 15])[0]
    if category in {"Shopping", "Loisirs", "Transport"}:
        return rng.choices(["carte", "en_ligne"], weights=[45, 55])[0]
    return rng.choices(["carte", "en_ligne"], weights=[60, 40])[0]


def fake_ip(rng: random.Random, country: str = "France") -> str:
    """Adresse IP fictive (documentation RFC 5737 pour l'étranger)."""
    if country != "France":
        return f"203.0.113.{rng.randint(2, 250)}"
    return f"82.{rng.randint(60, 70)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


# ---------------------------------------------------------------------------
# Création des utilisateurs
# ---------------------------------------------------------------------------
def create_user(session: Session, spec: dict, rng: random.Random) -> tuple[User, Account, list[Device]]:
    """Crée un utilisateur, ses comptes (courant + épargne) et ses appareils."""
    user = User(
        full_name=spec["full_name"],
        email=spec["email"],
        password_hash=hash_password(DEMO_PASSWORD),
        pin_hash=hash_pin(DEMO_PIN),
        phone=spec["phone"],
        city=spec["city"],
        country=spec["country"],
        monthly_income=spec["income"],
        risk_aversion=spec["risk_aversion"],
        created_at=now_utc() - timedelta(days=400),
        last_login_at=now_utc() - timedelta(hours=rng.randint(2, 40)),
    )
    session.add(user)
    session.flush()

    account = Account(
        user_id=user.id,
        kind="courant",
        label="Compte courant",
        iban_masked=f"FR76 3000 {user.id:04d} 0000 {rng.randint(1000, 9999)}",
        balance=0.0,
        currency="EUR",
    )
    savings = Account(
        user_id=user.id,
        kind="epargne",
        label="Livret d'épargne",
        iban_masked=f"FR76 3001 {user.id:04d} 0000 {rng.randint(1000, 9999)}",
        balance=round(spec["income"] * rng.uniform(0.6, 2.4), 2),
        currency="EUR",
    )
    session.add_all([account, savings])

    devices: list[Device] = []
    for label, platform in spec["devices"]:
        device = Device(
            user_id=user.id,
            fingerprint=generate_device_fingerprint(user.id, label),
            label=label,
            platform=platform,
            is_trusted=True,
            first_seen=now_utc() - timedelta(days=rng.randint(120, 380)),
            last_seen=now_utc() - timedelta(hours=rng.randint(1, 72)),
        )
        session.add(device)
        devices.append(device)

    session.flush()
    return user, account, devices


# ---------------------------------------------------------------------------
# Historique « normal »
# ---------------------------------------------------------------------------
def build_normal_history(rng: random.Random, spec: dict, devices: list[Device], account: Account) -> list[Transaction]:
    """
    Génère l'historique courant : salaires, loyer, abonnements et dépenses
    quotidiennes cohérentes avec le profil de l'utilisateur.
    """
    today = to_local(now_utc())
    months = spec["months"]
    start = today - timedelta(days=30 * months)
    scale = spec["income"] / 2800.0
    city = spec["city"]
    country = spec["country"]
    transactions: list[Transaction] = []

    # Commerçants « habituels » : l'utilisateur n'utilise qu'un sous-ensemble
    # du catalogue, ce qui rend les nouvelles enseignes réellement atypiques.
    habits: dict[str, list[str]] = {}
    for category, merchants in MERCHANTS.items():
        names = [merchant[0] for merchant in merchants]
        count = max(1, int(len(names) * rng.uniform(0.4, 0.7)))
        habits[category] = rng.sample(names, count)

    day = start
    while day.date() <= today.date():
        is_today = day.date() == today.date()
        # Nombre d'opérations du jour : plus élevé en semaine, modéré le week-end
        rate = 1.8 if day.weekday() < 5 else 1.4
        for _ in range(_poisson(rng, rate)):
            hour = pick_hour(rng, spec["chronotype"])
            if is_today and hour > today.hour:
                continue  # on n'écrit pas dans le futur

            category = weighted_choice(rng, CATEGORY_WEIGHTS)
            if category == "Retrait":
                merchant_name, low, high = "Retrait DAB", 20, 100
            elif category == "Abonnements":
                merchant_name = rng.choice(habits["Abonnements"])
                low, high = next(
                    (item[1], item[2]) for item in MERCHANTS["Abonnements"] if item[0] == merchant_name
                )
            else:
                merchant_name = rng.choice(habits[category])
                low, high = next((item[1], item[2]) for item in MERCHANTS[category] if item[0] == merchant_name)

            amount = sample_amount(rng, low, high, scale)
            channel = channel_for_category(rng, category)
            local_ts = day.replace(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59))

            # Ville : domicile dans 90 % des cas, sinon déplacement exceptionnel
            if rng.random() < 0.9 or not spec.get("secondary_cities"):
                txn_city = city
            else:
                txn_city = rng.choice(spec["secondary_cities"])
            txn_country = geo.country_for(txn_city, default=country)
            coords = geo.coords_for(txn_city)

            device = rng.choice(devices) if devices else None
            transactions.append(
                Transaction(
                    user_id=account.user_id,
                    account_id=account.id,
                    ts=_local_to_utc(local_ts),
                    amount=-amount,
                    currency="EUR",
                    label=f"{merchant_name} — {txn_city}",
                    merchant=merchant_name,
                    category=category,
                    channel=channel,
                    city=txn_city,
                    country=txn_country,
                    latitude=coords[0] if coords else None,
                    longitude=coords[1] if coords else None,
                    device_fingerprint=device.fingerprint if device else "",
                    ip_address=fake_ip(rng, txn_country),
                    meta={"simulation": True, "appareil": device.label if device else ""},
                )
            )
        day += timedelta(days=1)

    # --- Revenus et charges fixes --------------------------------------
    transactions.extend(_build_recurring(rng, spec, account, today, months, devices))
    transactions.sort(key=lambda txn: txn.ts)
    return transactions


def _poisson(rng: random.Random, mean: float) -> int:
    """Tirage d'une loi de Poisson (algorithme de Knuth, sans numpy)."""
    limit = pow(2.718281828459045, -mean)
    product, count = 1.0, 0
    while True:
        product *= rng.random()
        if product <= limit:
            return count
        count += 1


def calibrate_discretionary_spending(spec: dict, history: list[Transaction], target_ratio: float = 0.86) -> None:
    """
    Rééquilibre les dépenses « discrétionnaires » (hors loyer, salaire,
    abonnements) pour que le total des dépenses atteigne `target_ratio` des
    revenus de la période. Sans ce calage, un tirage aléatoire peut produire un
    utilisateur dépensant 30 % ou 200 % de ses revenus, ce qui rendrait les
    recommandations d'épargne absurdes.
    """
    income = float(spec["income"])
    months = int(spec["months"])
    target_total_spending = income * months * target_ratio

    recurring = sum(abs(txn.amount) for txn in history if txn.meta.get("recurrent") and txn.amount < 0)
    discretionary = [txn for txn in history if txn.amount < 0 and not txn.meta.get("recurrent")]
    current = sum(abs(txn.amount) for txn in discretionary)
    if current <= 0:
        return

    # Le budget discrétionnaire ne peut pas descendre sous 25 % des revenus
    target_discretionary = max(target_total_spending - recurring, income * months * 0.25)
    factor = max(0.55, min(1.6, target_discretionary / current))
    for txn in discretionary:
        txn.amount = -round(abs(txn.amount) * factor, 2)


def _local_to_utc(local_dt):
    """Conversion locale (Europe/Paris) -> UTC naïf (indépendante du fuseau machine)."""
    return local_dt.replace(tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)


def _build_recurring(
    rng: random.Random,
    spec: dict,
    account: Account,
    today,
    months: int,
    devices: list[Device],
) -> list[Transaction]:
    """Salaires, loyer, énergie et abonnements mensuels."""
    rows: list[Transaction] = []
    device = devices[0] if devices else None
    city, country = spec["city"], spec["country"]
    income = spec["income"]
    coords = geo.coords_for(city)

    for month_offset in range(months):
        reference = today - timedelta(days=30 * month_offset)
        month_day = reference.replace(day=1)
        # Le salaire tombe le 1er du mois : le mois en cours affiche donc
        # toujours des revenus, quel que soit le jour d'exécution du seed.
        pattern = [
            (1, "Virement salaire ACME", "Revenus", income, "virement"),
            (2, f"Loyer Agence Immo {city}", "Logement", -round(income * rng.uniform(0.28, 0.34), 2), "virement"),
            (5, "Netflix", "Loisirs", -round(rng.uniform(13.49, 19.99), 2), "prelevement"),
            (12, "Spotify", "Loisirs", -round(rng.uniform(10.99, 16.99), 2), "prelevement"),
            (15, "Orange Fibre", "Abonnements", -round(rng.uniform(29.99, 42.99), 2), "prelevement"),
            (8, "EDF", "Énergie", -round(rng.uniform(58, 128) * (income / 2800.0), 2), "prelevement"),
            (18, "Assurance Habitation MAIF", "Logement", -round(rng.uniform(18, 32), 2), "prelevement"),
        ]
        for day_number, label, category, amount, channel in pattern:
            try:
                local_ts = month_day.replace(day=day_number, hour=rng.randint(6, 10), minute=rng.randint(0, 59))
            except ValueError:  # mois plus court
                continue
            if local_ts > today or local_ts < today - timedelta(days=30 * months + 5):
                continue
            rows.append(
                Transaction(
                    user_id=account.user_id,
                    account_id=account.id,
                    ts=_local_to_utc(local_ts),
                    amount=round(amount, 2),
                    currency="EUR",
                    label=label,
                    merchant=label if amount > 0 else label.split(" Agence")[0].split(" Immo")[0],
                    category=category,
                    channel=channel,
                    city=city,
                    country=country,
                    latitude=coords[0] if coords else None,
                    longitude=coords[1] if coords else None,
                    device_fingerprint=device.fingerprint if device else "",
                    ip_address=fake_ip(rng, country),
                    meta={"simulation": True, "recurrent": True},
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Scénarios de fraude
# ---------------------------------------------------------------------------
FRAUD_SCENARIOS = [
    "montant_inhabituel",
    "appareil_inconnu",
    "localisation_impossible",
    "heure_inhabituelle",
    "categorie_risquee",
    "frequence_anormale",
]


def build_fraud_payment(rng: random.Random, spec: dict, scenario: str, profile: dict, existing: list[Transaction]) -> dict:
    """
    Construit l'opération frauduleuse correspondant à un scénario.

    Chaque scénario modifie des champs cohérents entre eux (lieu, appareil,
    heure, montant) afin que le moteur de risque puisse réellement les détecter.
    """
    city, country = spec["city"], spec["country"]
    stats = profile.get("amount_stats") or {}
    median = float(stats.get("median") or 40.0)
    max_amount = float(stats.get("max") or median * 8)
    device = rng.choice(existing)  # réutilisé uniquement pour l'empreinte
    local_now = to_local(now_utc())

    payment = {
        "amount": round(median * rng.uniform(2, 4), 2),
        "merchant": rng.choice(FRAUD_MERCHANTS["Électronique"]),
        "category": "Shopping",
        "city": city,
        "country": country,
        "channel": "en_ligne",
        "device_fingerprint": device,
        "device_label": "Appareil inconnu",
        "ts": now_utc() - timedelta(days=rng.uniform(0.2, 20)),
    }

    if scenario == "montant_inhabituel":
        payment["amount"] = round(max_amount * rng.uniform(1.4, 3.2), 2)
        payment["merchant"] = rng.choice(FRAUD_MERCHANTS["Électronique"])
        payment["category"] = "Shopping"

    elif scenario == "appareil_inconnu":
        payment["amount"] = round(median * rng.uniform(1.2, 2.5), 2)
        payment["device_fingerprint"] = f"unknown-{rng.getrandbits(48):012x}"
        payment["device_label"] = rng.choice(["Appareil inconnu", "Navigateur non identifié", "Terminal pirate"])

    elif scenario == "localisation_impossible":
        payment["city"] = rng.choice(["Lagos", "Moscou", "Dubaï", "New York", "Singapour", "Casablanca", "Pékin"])
        payment["country"] = {
            "Lagos": "Nigéria",
            "Moscou": "Russie",
            "Dubaï": "Émirats arabes unis",
            "New York": "États-Unis",
            "Singapour": "Singapour",
            "Casablanca": "Maroc",
            "Pékin": "Chine",
        }[payment["city"]]
        payment["amount"] = round(median * rng.uniform(2, 6), 2)
        payment["merchant"] = rng.choice(FRAUD_MERCHANTS["Transfert international"])
        payment["category"] = "Transfert international"
        payment["device_fingerprint"] = f"unknown-{rng.getrandbits(48):012x}"
        payment["device_label"] = "Appareil inconnu"

    elif scenario == "heure_inhabituelle":
        # 1h à 5h du matin, heure locale
        payment["amount"] = round(median * rng.uniform(1.5, 3.5), 2)
        payment["device_fingerprint"] = f"unknown-{rng.getrandbits(48):012x}"
        payment["device_label"] = "Appareil inconnu"

    elif scenario == "categorie_risquee":
        category = rng.choice(["Crypto", "Jeux d'argent"])
        payment["merchant"] = rng.choice(FRAUD_MERCHANTS[category])
        payment["category"] = category
        payment["amount"] = round(median * rng.uniform(4, 12), 2)
        payment["channel"] = "virement"

    elif scenario == "frequence_anormale":
        payment["amount"] = round(median * rng.uniform(0.8, 1.6), 2)
        payment["device_fingerprint"] = f"unknown-{rng.getrandbits(48):012x}"
        payment["device_label"] = "Appareil inconnu"

    # Ajustements temporels
    if scenario == "heure_inhabituelle":
        day = to_local(payment["ts"]).replace(hour=rng.randint(1, 4), minute=rng.randint(0, 59))
        payment["ts"] = _local_to_utc(day)
    elif rng.random() < 0.3:
        payment["ts"] = payment["ts"].replace(hour=local_now.hour if local_now.hour < 20 else 20)

    return payment


def inject_frauds(
    session: Session,
    rng: random.Random,
    spec: dict,
    user: User,
    account: Account,
    profile: dict,
    device_fingerprints: list[str],
    count: int = 3,
) -> list[Transaction]:
    """
    Injecte des opérations frauduleuses et **laisse le moteur de risque les
    juger** : le score, la décision et le statut sont calculés par le code de
    production, pas écrits en dur. C'est ce qui rend la démo crédible.
    """
    created: list[Transaction] = []
    scenarios = rng.sample(FRAUD_SCENARIOS, k=min(count, len(FRAUD_SCENARIOS)))

    for scenario in scenarios:
        payment = build_fraud_payment(rng, spec, scenario, profile, device_fingerprints)
        assessment = assess_payment(session, user, payment, profile)
        fraud_coords = geo.coords_for(payment["city"])
        payment["latitude"] = fraud_coords[0] if fraud_coords else None
        payment["longitude"] = fraud_coords[1] if fraud_coords else None

        completed = assessment.decision != "block"
        transaction = Transaction(
            user_id=user.id,
            account_id=account.id,
            ts=payment["ts"],
            amount=-float(payment["amount"]),
            currency="EUR",
            label=f"{payment['merchant']} — {payment['city']}",
            merchant=payment["merchant"],
            category=payment["category"],
            channel=payment["channel"],
            city=payment["city"],
            country=payment["country"],
            latitude=payment["latitude"],
            longitude=payment["longitude"],
            device_fingerprint=payment["device_fingerprint"],
            ip_address=fake_ip(rng, payment["country"]),
            risk_score=assessment.score,
            anomaly_score=assessment.anomaly_score,
            decision=assessment.decision,
            status="completed" if completed else "blocked",
            is_fraud=True,
            fraud_type=scenario,
            meta={
                "simulation": True,
                "fraude_simulee": scenario,
                "facteurs": assessment.factors,
                "explication": assessment.summary,
            },
        )
        session.add(transaction)
        session.flush()

        if completed:
            # L'argent a réellement quitté le compte (fraude réussie)
            account.balance = round(account.balance - float(payment["amount"]), 2)
            alerts_service.alert_for_high_anomaly(
                session,
                user_id=user.id,
                transaction=transaction,
                anomaly_score=max(assessment.anomaly_score, 0.45),
                reasons=[factor["detail"] for factor in assessment.factors[:3]],
                commit=False,
            )
        else:
            alerts_service.alert_for_blocked_payment(
                session, user_id=user.id, payment=payment, assessment=assessment
            )

        # Rafale : plusieurs opérations dans la foulée (scénario « fréquence »)
        if scenario == "frequence_anormale":
            for offset in range(2):
                follow_up = dict(payment)
                follow_up["ts"] = payment["ts"] + timedelta(minutes=4 * (offset + 1))
                follow_up["amount"] = round(float(payment["amount"]) * rng.uniform(0.6, 1.3), 2)
                follow_up["merchant"] = rng.choice(FRAUD_MERCHANTS["Électronique"])
                follow_assessment = assess_payment(session, user, follow_up, profile)
                follow_completed = follow_assessment.decision != "block"
                follow_txn = Transaction(
                    user_id=user.id,
                    account_id=account.id,
                    ts=follow_up["ts"],
                    amount=-float(follow_up["amount"]),
                    currency="EUR",
                    label=f"{follow_up['merchant']} — {follow_up['city']}",
                    merchant=follow_up["merchant"],
                    category=follow_up["category"],
                    channel=follow_up["channel"],
                    city=follow_up["city"],
                    country=follow_up["country"],
                    latitude=follow_up.get("latitude"),
                    longitude=follow_up.get("longitude"),
                    device_fingerprint=follow_up["device_fingerprint"],
                    ip_address=fake_ip(rng, follow_up["country"]),
                    risk_score=follow_assessment.score,
                    anomaly_score=follow_assessment.anomaly_score,
                    decision=follow_assessment.decision,
                    status="completed" if follow_completed else "blocked",
                    is_fraud=True,
                    fraud_type="frequence_anormale",
                    meta={"simulation": True, "facteurs": follow_assessment.factors},
                )
                session.add(follow_txn)
                session.flush()
                if follow_completed:
                    account.balance = round(account.balance - float(follow_up["amount"]), 2)
                else:
                    alerts_service.alert_for_blocked_payment(
                        session, user_id=user.id, payment=follow_up, assessment=follow_assessment
                    )
                created.append(follow_txn)

        created.append(transaction)
        session.flush()

    return created


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def reset_database(session: Session) -> None:
    """Vide toutes les tables (avant régénération)."""
    for model in (PaymentChallenge, Alert, BehavioralProfile, Transaction, Device, Account, AuditLog, User):
        session.query(model).delete()
    session.commit()


def seed_database(session: Session, force: bool = False, verbose: bool = True) -> dict:
    """
    Génère l'intégralité du jeu de démonstration.

    Retourne un rapport (nombre d'utilisateurs, transactions, fraudes, métriques).
    """
    rng = random.Random(SEED)
    existing = session.scalar(select(func.count(User.id))) or 0
    if existing and not force:
        return {"status": "already_seeded", "users": existing}

    if force:
        reset_database(session)

    created_users: list[User] = []
    total_transactions = 0

    for index, spec in enumerate(USER_SPECS):
        user, account, devices = create_user(session, spec, rng)
        fingerprints = [device.fingerprint for device in devices]

        history = build_normal_history(rng, spec, devices, account)
        session.add_all(history)
        session.flush()

        # Budget : on réajuste les dépenses discrétionnaires pour obtenir un
        # taux d'épargne réaliste (≈ 14 % des revenus sur la période).
        calibrate_discretionary_spending(spec, history)

        # Solde : on garantit un historique sans solde négatif (plancher 250 €)
        deltas = [txn.amount for txn in history]
        net = round(sum(deltas), 2)
        minimum, running = 0.0, 0.0
        for delta in deltas:
            running += delta
            minimum = min(minimum, running)

        target_final = max(spec["income"] * rng.uniform(0.8, 1.6), 1200.0)
        required_final = net + (250.0 - minimum)
        account.balance = round(max(target_final, required_final), 2)
        session.flush()

        created_users.append(user)
        total_transactions += len(history)
        if verbose:
            print(f"  · {spec['full_name']:<20} {len(history):>3} opérations  solde {account.balance:>9.2f} €")

    # --- Profil comportemental de référence (hors fraudes) ---------------
    profiles: dict[int, dict] = {}
    for user in created_users:
        profiles[user.id] = refresh_profile(session, user)

    # --- Entraînement du modèle d'anomalies ------------------------------
    if verbose:
        print("\n  Entraînement du modèle Isolation Forest…")
    training_report = train_detector(session)
    if verbose:
        print(f"  → {training_report['status']} : {training_report['message']}")

    # --- Injection des fraudes simulées ----------------------------------
    fraud_count = 0
    for index, (spec, user) in enumerate(zip(USER_SPECS, created_users)):
        account = session.scalar(select(Account).where(Account.user_id == user.id, Account.kind == "courant"))
        devices = session.scalars(select(Device).where(Device.user_id == user.id)).all()
        count = 3 if spec.get("primary") else rng.randint(2, 3)
        frauds = inject_frauds(
            session,
            rng,
            spec,
            user,
            account,
            profiles[user.id],
            [device.fingerprint for device in devices],
            count=count,
        )
        fraud_count += len(frauds)
        session.commit()

    # --- Profils finaux ---------------------------------------------------
    for user in created_users:
        refresh_profile(session, user)

    # --- Alertes de sensibilisation et journal d'audit -------------------
    for user in created_users:
        alerts_service.alert_security_tip(
            session,
            user_id=user.id,
            title="Bienvenue sur NexPay AI",
            message=(
                "Votre compte est protégé par une authentification dynamique : chaque paiement exige un code "
                "à usage unique valable 60 secondes, complété par votre code PIN lorsque le risque est élevé."
            ),
            severity="faible",
        )
        alerts_service.log_action(
            session,
            action="connexion",
            user_id=user.id,
            status="ok",
            detail="Connexion simulée depuis un appareil de confiance.",
            ip_address="82.64.10.24",
            user_agent="Mozilla/5.0 (seed)",
        )

    session.commit()

    # --- Évaluation du modèle sur la vérité terrain simulée --------------
    from app.ml.anomaly import evaluate
    from app.services.risk_engine import evaluate_decisions

    metrics = evaluate(session)
    decision_metrics = evaluate_decisions(session)

    frauds_labeled = session.scalar(select(func.count(Transaction.id)).where(Transaction.is_fraud.is_(True))) or 0
    blocked = session.scalar(select(func.count(Transaction.id)).where(Transaction.status == "blocked")) or 0
    escalated = (
        session.scalar(
            select(func.count(Transaction.id)).where(
                Transaction.is_fraud.is_(True), Transaction.decision.in_(("verify", "block"))
            )
        )
        or 0
    )
    report = {
        "status": "ok",
        "users": len(created_users),
        "transactions": total_transactions + fraud_count,
        "frauds": frauds_labeled,
        "blocked": blocked,
        "escalated": escalated,
        "training": training_report,
        "metrics": metrics,
        "decision_metrics": decision_metrics,
    }

    if verbose:
        print(
            f"\n  ✔ {report['users']} utilisateurs, {report['transactions']} opérations "
            f"dont {report['frauds']} fraudes simulées : {escalated} détectées"
            f" ({blocked} bloquées, {escalated - blocked} envoyées en vérification renforcée)."
        )
        if metrics:
            print(
                f"  ✔ Détection IA : précision {metrics.get('precision')}, rappel {metrics.get('recall')}, "
                f"F1 {metrics.get('f1')}, AUC {metrics.get('roc_auc')} "
                f"(seuil optimal {metrics.get('best_threshold')})."
            )
        if decision_metrics:
            print(
                f"  ✔ Moteur de décision : {decision_metrics.get('taux_detection', 0) * 100:.0f} % des fraudes "
                f"bloquées ou escaladées, {decision_metrics.get('taux_friction', 0) * 100:.0f} % de friction "
                "sur les paiements légitimes."
            )
        print("\n  Compte de démonstration : demo@nexpay.fr / Demo1234!   (code PIN : 4321)")
    return report


def main() -> None:
    """Point d'entrée en ligne de commande : `python -m app.seed [--force]`."""
    parser = argparse.ArgumentParser(description="Génère les données de démonstration NexPay AI.")
    parser.add_argument("--force", action="store_true", help="Efface les données existantes avant de régénérer.")
    arguments = parser.parse_args()

    print(f"\n{settings.app_name} — génération des données de démonstration")
    init_db()
    session = SessionLocal()
    try:
        report = seed_database(session, force=arguments.force)
        if report["status"] == "already_seeded":
            print(
                f"  La base contient déjà {report['users']} utilisateur(s). "
                "Utilisez --force pour régénérer.\n"
            )
    finally:
        session.close()


if __name__ == "__main__":
    main()
