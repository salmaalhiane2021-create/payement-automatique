"""
Moteur de risque : décide si un paiement est accepté, vérifié ou bloqué.

Fonctionnement
--------------
Le score final (0 → 100) additionne deux contributions :

1. **Règles métier pondérées** (~80 points possibles) — montant inhabituel,
   heure atypique, appareil inconnu, lieu jamais vu, déplacement impossible,
   fréquence anormale, nouveau bénéficiaire, catégorie sensible, canal
   inhabituel, opération qui vide le compte…
2. **Score d'anomalie IA** (~20 points) — Isolation Forest, voir
   `app/ml/anomaly.py`.

Décision (seuils configurables dans `.env`) :

    score <  35         → « accept » : paiement accepté (code OTP exigé)
    35 ≤ score < 70     → « verify » : vérification renforcée (OTP + code PIN)
    score ≥ 70          → « block »  : paiement refusé

Trois règles provoquent un blocage immédiat quelle que soit la somme :
un déplacement physiquement impossible, des fonds insuffisants, ou un score
au-delà du seuil de blocage.

Politique de « step-up » : certains signaux déclenchent obligatoirement une
vérification renforcée même si le score reste modéré (appareil inconnu, pays
jamais vu, catégorie à risque, déplacement très rapide). C'est le principe
retenu par les banques : un montant normal depuis un appareil inconnu ne peut
jamais être accepté sans second facteur.

Chaque facteur est renvoyé avec un libellé français : l'utilisateur comprend
pourquoi sa transaction est suspecte (exigence RGPD/IA explicable).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import now_utc
from app.ml.anomaly import quick_statistical_risk, score_context
from app.ml.features import build_features
from app.models import Account, Transaction, User
from app.services import geo
from app.services.behavior import build_profile, to_local

# ---------------------------------------------------------------------------
# Pondérations des règles (points maximum par facteur)
# ---------------------------------------------------------------------------
WEIGHTS = {
    "montant_inhabituel": 25,
    "montant_eleve": 12,
    "solde_insuffisant": 0,          # blocage immédiat, hors barème
    "compte_presque_vide": 8,
    "heure_inhabituelle": 10,
    "appareil_inconnu": 18,
    "localisation_inhabituelle": 14,
    # « zone_inhabituelle » : écart par rapport à l'historique **de
    # l'utilisateur** (zones d'où il a l'habitude d'opérer). Le pays n'est
    # jamais un motif de suspicion en soi : aucune nationalité n'intervient,
    # aucune restriction n'est fondée sur l'origine ou le pays de résidence.
    "zone_inhabituelle": 8,
    "velocite_impossible": 25,
    "velocite_suspecte": 12,
    "frequence_anormale": 15,
    "nouveau_beneficiaire": 5,
    "categorie_sensible": 12,
    "canal_inhabituel": 6,
    "anomalie_ia": 20,
}

# Catégories de commerce intrinsèquement risquées (blanchiment, addiction…)
SENSITIVE_CATEGORIES = {"Crypto", "Jeux d'argent", "Transfert international"}

DECISION_LABELS = {
    "accept": "Paiement accepté",
    "verify": "Vérification renforcée requise",
    "block": "Paiement bloqué",
}


@dataclass
class RiskAssessment:
    """Résultat complet de l'analyse d'une opération."""

    score: float = 0.0
    rules_score: float = 0.0
    anomaly_score: float = 0.0
    decision: str = "accept"
    factors: list[dict] = field(default_factory=list)
    hard_block: bool = False
    features: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    summary: str = ""
    step_up: list[str] = field(default_factory=list)

    @property
    def decision_label(self) -> str:
        return DECISION_LABELS.get(self.decision, self.decision)

    @property
    def require_pin(self) -> bool:
        return self.decision == "verify"


# ---------------------------------------------------------------------------
# Helpers de contexte
# ---------------------------------------------------------------------------
def checking_account(db: Session, user: User) -> Account | None:
    """Compte courant de l'utilisateur (compte débité lors d'un paiement)."""
    return db.scalar(
        select(Account).where(Account.user_id == user.id, Account.kind == "courant").order_by(Account.id)
    )


def transactions_last_hour(db: Session, user: User, reference=None) -> int:
    """Nombre d'opérations réalisées dans l'heure précédant `reference`."""
    reference = reference or now_utc()
    window_start = reference - timedelta(hours=1)
    count = db.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.user_id == user.id,
            Transaction.ts >= window_start,
            Transaction.ts <= reference,
        )
    )
    return int(count or 0)


def last_transaction(db: Session, user: User, before=None) -> Transaction | None:
    """Dernière opération enregistrée avant `before` (pour la vélocité)."""
    before = before or now_utc()
    return db.scalar(
        select(Transaction)
        .where(Transaction.user_id == user.id, Transaction.ts <= before)
        .order_by(Transaction.ts.desc())
        .limit(1)
    )


def merchant_is_new(profile: dict, merchant: str) -> bool:
    """Le bénéficiaire est-il inconnu ? (nécessite un historique suffisant)."""
    if profile.get("cold_start") or profile.get("count", 0) < 20:
        return False
    known = {row["merchant"].lower() for row in profile.get("known_merchants", [])}
    return merchant.strip().lower() not in known


# ---------------------------------------------------------------------------
# Moteur
# ---------------------------------------------------------------------------
def assess_payment(db: Session, user: User, payment: dict, profile: dict) -> RiskAssessment:
    """
    Analyse une opération avant validation.

    `payment` doit contenir : merchant, amount, category, city, country,
    channel, device_fingerprint, ts (UTC naïf) et éventuellement latitude /
    longitude. Aucune donnée bancaire sensible n'est manipulée.
    """
    ts = payment.get("ts") or now_utc()
    # L'heure et le jour sont interprétés dans le fuseau de l'utilisateur :
    # une opération « normale » pour un client en Asie ne doit pas être jugée
    # nocturne parce que le serveur est configuré sur un autre fuseau.
    local_ts = to_local(ts, user.timezone)
    amount = float(payment.get("amount") or 0.0)
    merchant = (payment.get("merchant") or "").strip()
    city = (payment.get("city") or "").strip()
    country = (payment.get("country") or "").strip() or geo.country_for(city)
    channel = payment.get("channel") or "en_ligne"

    # --- Contexte : position précédente, fréquence, matériel -------------
    previous = last_transaction(db, user, before=ts)
    latitude = payment.get("latitude")
    longitude = payment.get("longitude")
    if latitude is None or longitude is None:
        coords = geo.coords_for(city, country)
        if coords:
            latitude, longitude = coords

    velocity, distance = geo.travel_velocity_kmh(
        previous.latitude if previous else None,
        previous.longitude if previous else None,
        previous.ts if previous else None,
        latitude,
        longitude,
        ts,
    )

    txn_count_1h = transactions_last_hour(db, user, reference=ts)
    account = checking_account(db, user)
    balance = float(account.balance) if account else 0.0

    context = {
        "amount": amount,
        "hour": local_ts.hour,
        "weekday": local_ts.weekday(),
        "device_fingerprint": payment.get("device_fingerprint") or "",
        "city": city,
        "country": country,
        "channel": channel,
        "velocity_kmh": velocity,
        "txn_count_1h": txn_count_1h,
        "distance_km": distance,
    }

    # --- Analyse IA ------------------------------------------------------
    anomaly_score, features, anomaly_reasons = score_context(context, profile)

    assessment = RiskAssessment(
        anomaly_score=round(float(anomaly_score), 4),
        features=features,
        context=context,
    )
    factors = assessment.factors
    # Signaux imposant une vérification renforcée même si le score reste modéré
    step_up_codes: set[str] = set()

    def add(code: str, points: float, detail: str, severity: str) -> None:
        """Ajoute un facteur pondéré au score."""
        if points <= 0:
            return
        factors.append(
            {
                "code": code,
                "label": _LABELS.get(code, code),
                "detail": detail,
                "weight": round(points, 1),
                "severity": severity,
            }
        )

    # --- 1. Montant comparé aux habitudes -------------------------------
    stats = profile.get("amount_stats") or {}
    cold_start = bool(profile.get("cold_start")) or profile.get("count", 0) == 0
    if not cold_start and stats.get("median"):
        median = float(stats["median"])
        spread = max(1.4826 * float(stats.get("mad") or 0.0), 15.0)
        zscore = (amount - median) / spread
        if zscore > 2.0:
            points = min(WEIGHTS["montant_inhabituel"], WEIGHTS["montant_inhabituel"] * (zscore - 2.0) / 8.0)
            # Le montant reste dans l'historique connu : on plafonne la pénalité.
            if amount <= float(stats.get("max") or 0.0):
                points = min(points, 15.0)
            severity = "critique" if zscore > 6 else ("eleve" if zscore > 4 else "moyen")
            add(
                "montant_inhabituel",
                points,
                f"{amount:.2f} € contre une médiane de {median:.2f} € ({amount / median:.1f}× vos habitudes).",
                severity,
            )
    elif cold_start and amount > 300:
        add("montant_inhabituel", min(15.0, amount / 100.0), "Aucun historique : contrôle renforcé sur ce montant.", "moyen")

    # --- 2. Montant absolu ---------------------------------------------
    for threshold, points, severity in ((2000, 12, "eleve"), (1000, 9, "moyen"), (500, 5, "moyen"), (300, 2, "faible")):
        if amount >= threshold:
            add("montant_eleve", points, f"Montant supérieur à {threshold} €.", severity)
            break

    # --- 3. Fonds disponibles ------------------------------------------
    if account is not None:
        if amount > balance:
            assessment.hard_block = True
            factors.append(
                {
                    "code": "solde_insuffisant",
                    "label": _LABELS["solde_insuffisant"],
                    "detail": f"Montant demandé ({amount:.2f} €) supérieur au solde disponible ({balance:.2f} €).",
                    "weight": 0.0,
                    "severity": "critique",
                }
            )
        elif balance > 0:
            ratio = amount / balance
            if ratio > 0.6:
                add("compte_presque_vide", 8, f"Opération représentant {ratio * 100:.0f} % du solde du compte.", "eleve")
            elif ratio > 0.35:
                add("compte_presque_vide", 4, f"Opération représentant {ratio * 100:.0f} % du solde du compte.", "moyen")

    # --- 4. Horaire ------------------------------------------------------
    hour_unusual = float(features.get("hour_unusual") or 0.0)
    night = bool(features.get("night_time"))
    hour_points = WEIGHTS["heure_inhabituelle"] * hour_unusual
    if night:
        hour_points = max(hour_points, 6.0)
    add(
        "heure_inhabituelle",
        hour_points,
        f"Paiement à {context['hour']}h, en dehors de vos horaires habituels."
        if hour_unusual >= 0.5
        else f"Paiement de nuit à {context['hour']}h.",
        "eleve" if hour_unusual >= 0.8 else "moyen",
    )

    # --- 5. Appareil -----------------------------------------------------
    if features.get("new_device"):
        step_up_codes.add("appareil_inconnu")
        add(
            "appareil_inconnu",
            WEIGHTS["appareil_inconnu"],
            f"Appareil « {payment.get('device_label') or 'inconnu'} » jamais utilisé sur ce compte.",
            "eleve",
        )

    # --- 6. Localisation -------------------------------------------------
    if features.get("new_location"):
        add("localisation_inhabituelle", 8, f"Première opération depuis {city}.", "moyen")
    known_countries = {c.lower() for c in (profile.get("known_countries") or [])}
    if country and country.lower() not in known_countries:
        # Signal strictement comportemental : « zone jamais observée sur **ce**
        # compte ». Aucune nationalité n'est demandée ni utilisée ; le motif
        # serait le même pour un client de n'importe quel pays.
        step_up_codes.add("zone_inhabituelle")
        add(
            "zone_inhabituelle",
            WEIGHTS["zone_inhabituelle"],
            f"Opération depuis une zone encore jamais observée sur votre compte ({country}) : "
            "signal fondé sur votre historique, pas sur votre origine.",
            "moyen",
        )

    # --- 7. Vélocité géographique ---------------------------------------
    if velocity >= geo.IMPOSSIBLE_SPEED_KMH and distance >= 150:
        assessment.hard_block = True
        add(
            "velocite_impossible",
            WEIGHTS["velocite_impossible"],
            f"Déplacement de {distance:.0f} km ({velocity:.0f} km/h) depuis votre dernière opération de "
            f"{to_local(previous.ts, user.timezone).strftime('%H:%M')} : physiquement impossible.",
            "critique",
        )
    elif velocity >= geo.SUSPICIOUS_SPEED_KMH and distance >= 100:
        step_up_codes.add("velocite_suspecte")
        add(
            "velocite_suspecte",
            WEIGHTS["velocite_suspecte"],
            f"Déplacement très rapide : {distance:.0f} km en moins de "
            f"{max(1, int(abs((ts - previous.ts).total_seconds()) / 60))} minutes.",
            "eleve",
        )

    # --- 8. Fréquence ----------------------------------------------------
    if txn_count_1h >= 2:
        points = min(WEIGHTS["frequence_anormale"], (txn_count_1h - 1) * 4.0)
        add("frequence_anormale", points, f"{txn_count_1h} opérations sur la dernière heure.", "eleve" if txn_count_1h >= 4 else "moyen")

    # --- 9. Bénéficiaire -------------------------------------------------
    if merchant_is_new(profile, merchant):
        add("nouveau_beneficiaire", WEIGHTS["nouveau_beneficiaire"], f"Première opération vers « {merchant} ».", "faible")

    # --- 10. Catégorie sensible -----------------------------------------
    if payment.get("category") in SENSITIVE_CATEGORIES:
        step_up_codes.add("categorie_sensible")
        add("categorie_sensible", WEIGHTS["categorie_sensible"], f"Catégorie à risque : {payment.get('category')}.", "eleve")

    # --- 11. Canal de paiement ------------------------------------------
    if features.get("new_channel"):
        add("canal_inhabituel", WEIGHTS["canal_inhabituel"], f"Canal « {channel} » jamais utilisé.", "faible")

    # --- 12. Score IA ----------------------------------------------------
    if anomaly_score >= 0.15:
        add(
            "anomalie_ia",
            WEIGHTS["anomalie_ia"] * float(anomaly_score),
            "Comportement atypique détecté par le modèle Isolation Forest"
            + (f" : {anomaly_reasons[0]}" if anomaly_reasons else "."),
            "eleve" if anomaly_score >= 0.5 else "moyen",
        )

    # --- Agrégation et décision -----------------------------------------
    rules_only = sum(factor["weight"] for factor in factors if factor["code"] != "anomalie_ia")
    assessment.rules_score = round(min(100.0, rules_only), 1)
    assessment.score = round(min(100.0, sum(factor["weight"] for factor in factors)), 1)

    if assessment.hard_block or assessment.score >= settings.risk_verify_max:
        assessment.decision = "block"
    elif assessment.score >= settings.risk_accept_max:
        assessment.decision = "verify"
    else:
        assessment.decision = "accept"

    # Politique de vérification renforcée : un signal critique impose au moins
    # une validation à deux facteurs (jamais d'acceptation automatique).
    assessment.step_up = sorted(step_up_codes)
    if assessment.step_up and assessment.decision == "accept":
        assessment.decision = "verify"
        for code in assessment.step_up:
            factors.append(
                {
                    "code": f"step_up_{code}",
                    "label": "Vérification renforcée imposée",
                    "detail": f"Signal critique ({_LABELS.get(code, code)}) : validation à deux facteurs obligatoire.",
                    "weight": 0.0,
                    "severity": "info",
                }
            )

    factors.sort(key=lambda factor: factor["weight"], reverse=True)
    assessment.summary = _build_summary(assessment, payment, anomaly_reasons)
    return assessment


_LABELS = {
    "montant_inhabituel": "Montant inhabituel",
    "montant_eleve": "Montant élevé",
    "solde_insuffisant": "Fonds insuffisants",
    "compte_presque_vide": "Solde mis en danger",
    "heure_inhabituelle": "Heure inhabituelle",
    "appareil_inconnu": "Appareil inconnu",
    "localisation_inhabituelle": "Lieu inhabituel",
    "zone_inhabituelle": "Zone géographique inhabituelle",
    "velocite_impossible": "Déplacement impossible",
    "velocite_suspecte": "Déplacement très rapide",
    "frequence_anormale": "Rythme d'opérations anormal",
    "nouveau_beneficiaire": "Nouveau bénéficiaire",
    "categorie_sensible": "Catégorie à risque",
    "canal_inhabituel": "Canal inhabituel",
    "anomalie_ia": "Anomalie comportementale (IA)",
}


def _build_summary(assessment: RiskAssessment, payment: dict, anomaly_reasons: list[str]) -> str:
    """Phrase de synthèse affichée à l'utilisateur."""
    amount = float(payment.get("amount") or 0.0)
    top = [factor["label"].lower() for factor in assessment.factors if factor["weight"] >= 5][:3]

    if assessment.decision == "block":
        if assessment.hard_block:
            reason = next(
                (factor["detail"] for factor in assessment.factors if factor["severity"] == "critique"),
                "Opération jugée trop risquée.",
            )
            return f"Paiement refusé par sécurité. {reason}"
        return (
            f"Paiement de {amount:.2f} € bloqué : score de risque {assessment.score:.0f}/100"
            + (f" ({', '.join(top)})." if top else ".")
        )

    if assessment.decision == "verify":
        reasons = top or ["comportement inhabituel"]
        if assessment.step_up:
            reasons = [f"vérification imposée : {', '.join(_LABELS.get(code, code).lower() for code in assessment.step_up)}"]
        return (
            f"Score de risque {assessment.score:.0f}/100. Signaux détectés : {', '.join(reasons)}. "
            "Validez avec le code à usage unique ET votre code PIN."
        )

    return (
        f"Score de risque {assessment.score:.0f}/100 : opération conforme à vos habitudes. "
        "Un code à usage unique reste nécessaire pour valider."
    )


def statistical_risk(profile: dict, amount: float) -> float:
    """Indice d'écart de montant (0-1), utilisé pour la jauge du tableau de bord."""
    return quick_statistical_risk(amount, profile)


def preview_features(profile: dict, payment: dict, timezone_name: str | None = None) -> dict:
    """Variables calculées pour un paiement (utile aux tests et au débogage)."""
    ts = payment.get("ts") or now_utc()
    context = {
        "amount": payment.get("amount"),
        "hour": to_local(ts, timezone_name).hour,
        "device_fingerprint": payment.get("device_fingerprint"),
        "city": payment.get("city"),
        "channel": payment.get("channel"),
        "velocity_kmh": payment.get("velocity_kmh", 0.0),
        "txn_count_1h": payment.get("txn_count_1h", 0),
    }
    return build_features(context, profile)


# ---------------------------------------------------------------------------
# Évaluation au niveau de la décision métier
# ---------------------------------------------------------------------------
def _payment_from_transaction(transaction) -> dict:
    """Reconstruit le contexte d'un paiement à partir d'une opération enregistrée."""
    return {
        "merchant": transaction.merchant,
        "amount": abs(float(transaction.amount)),
        "category": transaction.category,
        "city": transaction.city,
        "country": transaction.country,
        "channel": transaction.channel,
        "device_fingerprint": transaction.device_fingerprint,
        "device_label": (transaction.meta or {}).get("appareil", "Appareil"),
        "latitude": transaction.latitude,
        "longitude": transaction.longitude,
        "ts": transaction.ts,
    }


def evaluate_decisions(db: Session, sample_normal: int = 200, seed: int = 7) -> dict:
    """
    Évalue le **moteur de décision complet** sur l'historique étiqueté.

    On rejoue le moteur de risque sur toutes les fraudes connues et sur un
    échantillon aléatoire d'opérations légitimes. C'est la mesure qui compte
    réellement pour l'utilisateur :

    * **taux de détection** : part des fraudes bloquées ou escaladées ;
    * **taux de friction** : part des paiements légitimes qui subissent une
      vérification ou un blocage (coût pour le client).

    Le score d'anomalie seul (AUC) mesure la qualité du modèle ; cette
    évaluation mesure la qualité du **système** complet (règles + IA).
    """
    import random as _random

    users = {user.id: user for user in db.scalars(select(User)).all()}
    profiles: dict[int, dict] = {}

    frauds = list(db.scalars(select(Transaction).where(Transaction.is_fraud.is_(True))).all())
    # Seuls les paiements (montants négatifs) sont rejoués : un virement reçu
    # n'est pas une opération que le moteur a à accepter ou refuser.
    legit_query = select(Transaction).where(
        Transaction.is_fraud.is_(False), Transaction.status != "blocked", Transaction.amount < 0
    )
    legit_pool = list(db.scalars(legit_query).all())
    rng = _random.Random(seed)
    legitimate = rng.sample(legit_pool, k=min(sample_normal, len(legit_pool)))

    counters = {
        "fraudes_bloquees": 0,
        "fraudes_escaladees": 0,
        "fraudes_acceptees": 0,
        "legitimes_acceptees": 0,
        "legitimes_escaladees": 0,
        "legitimes_bloquees": 0,
    }
    fraud_scores: list[float] = []
    legit_scores: list[float] = []

    for label, transactions in ((True, frauds), (False, legitimate)):
        for transaction in transactions:
            user = users.get(transaction.user_id)
            if user is None:
                continue
            if transaction.user_id not in profiles:
                profiles[transaction.user_id] = build_profile(db, user)
            assessment = assess_payment(db, user, _payment_from_transaction(transaction), profiles[transaction.user_id])

            if label:
                fraud_scores.append(assessment.score)
                if assessment.decision == "block":
                    counters["fraudes_bloquees"] += 1
                elif assessment.decision == "verify":
                    counters["fraudes_escaladees"] += 1
                else:
                    counters["fraudes_acceptees"] += 1
            else:
                legit_scores.append(assessment.score)
                if assessment.decision == "block":
                    counters["legitimes_bloquees"] += 1
                elif assessment.decision == "verify":
                    counters["legitimes_escaladees"] += 1
                else:
                    counters["legitimes_acceptees"] += 1

    n_fraud = len(fraud_scores)
    n_legit = len(legit_scores)
    detected = counters["fraudes_bloquees"] + counters["fraudes_escaladees"]
    friction = counters["legitimes_escaladees"] + counters["legitimes_bloquees"]

    return {
        "n_fraudes": n_fraud,
        "n_legitimes": n_legit,
        **counters,
        "taux_detection": round(detected / n_fraud, 3) if n_fraud else 0.0,
        "taux_friction": round(friction / n_legit, 3) if n_legit else 0.0,
        "score_moyen_fraude": round(sum(fraud_scores) / n_fraud, 1) if n_fraud else 0.0,
        "score_moyen_legitime": round(sum(legit_scores) / n_legit, 1) if n_legit else 0.0,
    }
