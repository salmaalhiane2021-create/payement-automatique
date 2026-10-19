"""
Détection d'anomalies par Isolation Forest (scikit-learn).

Pourquoi l'Isolation Forest ?
-----------------------------
C'est un modèle **non supervisé** : il n'a pas besoin d'exemples de fraude
étiquetés (qui sont rares dans la réalité). Il isole les observations
atypiques en construisant des arbres de décision aléatoires : une transaction
« anormale » se retrouve isolée en peu de découpages, donc sa profondeur
moyenne dans les arbres est faible.

Pipeline
--------
1. `train_detector` : construit la matrice des variables (voir `features.py`)
   à partir de l'historique **hors fraudes connues**, normalise
   (StandardScaler) puis entraîne l'Isolation Forest.
2. Les scores bruts (`decision_function`) sont recalibrés en probabilité
   d'anomalie entre 0 et 1 (0,1 = normal, > 0,5 = très atypique).
3. `score_context` renvoie le score + les raisons expliquées en français.

Le modèle est sérialisé (joblib) dans `app/ml/artifacts/` et rechargé au
démarrage : le serveur ne réentraîne donc pas à chaque requête.
"""

from __future__ import annotations

import io
import os
import socket
import time
from collections import deque

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import now_utc
from app.ml.artifact_store import (
    SUPPORTED_ARTIFACT_FORMAT,
    ArtifactStore,
    build_store,
)
from app.ml.features import (
    FEATURE_LABELS_FR,
    FEATURE_NAMES,
    FEATURE_VERSION,
    build_features,
    context_from_transaction,
    to_vector,
)
from app.models import Transaction, User
from app.services import geo
from app.services.behavior import build_profile

MODEL_VERSION = f"isolation-forest-{SUPPORTED_ARTIFACT_FORMAT}.{FEATURE_VERSION}"

# Nombre de points de quantiles empiriques conservés pour la calibration.
QUANTILE_POINTS = 201

# Exposant de la courbe de calibration : plus il est élevé, plus le score est
# réservé aux opérations réellement situées dans la queue de distribution.
# Quantiles de référence (q = position de l'opération dans l'historique) :
#   q = 0,50 → score 0,03   (opération parfaitement habituelle)
#   q = 0,90 → score 0,59
#   q = 0,99 → score 0,95   (1 % d'opérations plus atypiques seulement)
CALIBRATION_SHARPNESS = 5.0


# ---------------------------------------------------------------------------
# Détecteur
# ---------------------------------------------------------------------------
class AnomalyDetector:
    """Enveloppe autour de l'Isolation Forest + sa normalisation."""

    def __init__(self) -> None:
        self.model: IsolationForest | None = None
        self.scaler: StandardScaler | None = None
        self.metadata: dict = {}
        # Raison du dernier refus de chargement (version incompatible, artefact
        # illisible…) : affichée dans l'interface « Modèle & données ».
        self.load_error: str = ""

    # -- État ------------------------------------------------------------
    @property
    def available(self) -> bool:
        """Le modèle est-il chargé et utilisable ?"""
        return self.model is not None and self.scaler is not None

    @property
    def training_mean(self) -> np.ndarray | None:
        return self.metadata.get("train_mean")

    @property
    def training_std(self) -> np.ndarray | None:
        return self.metadata.get("train_std")

    # -- Persistance -----------------------------------------------------
    def save(self, store: ArtifactStore | None = None) -> None:
        """Sérialise le modèle via le stockage configuré (local ou partagé)."""
        store = store or get_store()
        buffer = io.BytesIO()
        joblib.dump(
            {
                "model": self.model,
                "scaler": self.scaler,
                "metadata": self.metadata,
            },
            buffer,
        )
        store.write(buffer.getvalue())

    def load(self, store: ArtifactStore | None = None) -> bool:
        """
        Charge le modèle depuis le stockage ; False si absent ou incompatible.

        Vérifications explicites (évite d'appliquer un modèle entraîné sur un
        schéma de variables différent) :

        * format du bundle (`artifact_format`) ;
        * liste et ordre des variables (`feature_names`) ;
        * **version des variables** (`feature_version`) ;
        * version du modèle (`model_version`).
        """
        store = store or get_store()
        self.load_error = ""
        payload = store.read()
        if payload is None:
            self.load_error = f"aucun artefact dans {store.describe()}"
            return False
        try:
            bundle = joblib.load(io.BytesIO(payload))
        except Exception as error:  # pragma: no cover - artefact corrompu
            self.load_error = f"artefact illisible ({type(error).__name__})"
            return False

        metadata = (bundle or {}).get("metadata") or {}
        checks = (
            (metadata.get("artifact_format") == SUPPORTED_ARTIFACT_FORMAT,
             f"format d'artefact {metadata.get('artifact_format')} ≠ {SUPPORTED_ARTIFACT_FORMAT}"),
            (metadata.get("feature_names") == FEATURE_NAMES,
             "schéma de variables différent"),
            (metadata.get("feature_version") == FEATURE_VERSION,
             f"version de variables {metadata.get('feature_version')} ≠ {FEATURE_VERSION}"),
            (metadata.get("model_version") == MODEL_VERSION,
             f"version de modèle {metadata.get('model_version')} ≠ {MODEL_VERSION}"),
        )
        for valid, reason in checks:
            if not valid:
                self.load_error = f"{reason} : réentraînement nécessaire"
                return False

        self.model = bundle["model"]
        self.scaler = bundle["scaler"]
        self.metadata = metadata
        return True

    # -- Entraînement ----------------------------------------------------
    def fit(self, matrix: np.ndarray, contamination: float) -> dict:
        """Entraîne le modèle sur une matrice de variables (n_samples × n_features)."""
        started = time.perf_counter()

        self.scaler = StandardScaler()
        scaled = self.scaler.fit_transform(matrix)

        self.model = IsolationForest(
            n_estimators=200,
            max_samples="auto",
            contamination=float(contamination),
            random_state=42,
            n_jobs=-1,
        )
        self.model.fit(scaled)

        # Calibration : on mémorise la distribution empirique des scores bruts
        # sur les données d'entraînement. Un score futur sera converti en
        # « percentile » (position dans l'historique), puis élevé à une
        # puissance pour concentrer le score sur la vraie queue de distribution.
        raw = -self.model.decision_function(scaled)
        self.metadata = {
            # --- Identité et compatibilité de l'artefact -------------------
            "artifact_format": SUPPORTED_ARTIFACT_FORMAT,
            "feature_version": FEATURE_VERSION,
            "model_version": MODEL_VERSION,
            "app_version": settings.app_version,
            # --- Schéma et échantillon d'entraînement ----------------------
            "feature_names": FEATURE_NAMES,
            "n_samples": int(matrix.shape[0]),
            "n_features": int(matrix.shape[1]),
            "contamination": float(contamination),
            "trained_at": now_utc().isoformat() + "Z",
            "anomaly_mean": float(raw.mean()),
            "anomaly_std": float(raw.std() or 1e-9),
            "anomaly_quantiles": np.quantile(raw, np.linspace(0.0, 1.0, QUANTILE_POINTS)).tolist(),
            "calibration_sharpness": CALIBRATION_SHARPNESS,
            "train_mean": matrix.mean(axis=0).tolist(),
            "train_std": (matrix.std(axis=0) + 1e-9).tolist(),
            "fit_seconds": round(time.perf_counter() - started, 3),
            "algorithm": "IsolationForest(scikit-learn)",
        }
        return self.metadata

    # -- Inférence -------------------------------------------------------
    def score_matrix(self, matrix: np.ndarray) -> np.ndarray:
        """Scores d'atypisme (0-1) pour une matrice de variables."""
        if not self.available:
            return np.zeros(matrix.shape[0])
        scaled = self.scaler.transform(matrix)
        raw = -self.model.decision_function(scaled)

        quantiles = self.metadata.get("anomaly_quantiles")
        if not quantiles:
            # Repli : normalisation par la moyenne et l'écart-type d'entraînement
            mean = self.metadata.get("anomaly_mean", 0.0)
            std = self.metadata.get("anomaly_std", 1.0) or 1.0
            return 1.0 / (1.0 + np.exp(-((raw - mean) / std - 3.0) / 1.2))

        reference = np.asarray(quantiles, dtype=float)
        percentiles = np.linspace(0.0, 1.0, len(reference))
        positions = np.interp(raw, reference, percentiles)
        sharpness = float(self.metadata.get("calibration_sharpness", CALIBRATION_SHARPNESS))
        return np.power(np.clip(positions, 0.0, 1.0), sharpness)

    def score_features(self, features: dict[str, float]) -> float:
        """Probabilité d'anomalie pour une seule opération."""
        if not self.available:
            return _statistical_fallback(features)
        matrix = np.asarray([to_vector(features)], dtype=float)
        return float(self.score_matrix(matrix)[0])

    def rescore_transactions(self, db: Session, user_id: int | None = None) -> int:
        """Recalcule les scores d'anomalie des transactions déjà en base."""
        statement = select(Transaction).where(Transaction.amount < 0)
        if user_id is not None:
            statement = statement.where(Transaction.user_id == user_id)
        transactions = list(db.scalars(statement.order_by(Transaction.user_id, Transaction.ts)).all())
        if not transactions:
            return 0

        users = list(db.scalars(select(User)).all())
        profiles = {user.id: build_profile(db, user) for user in users}
        timezones = {user.id: user.timezone for user in users}
        by_user: dict[int, list[Transaction]] = {}
        for txn in transactions:
            by_user.setdefault(txn.user_id, []).append(txn)

        vectors: list[list[float]] = []
        for user_id_key, items in by_user.items():
            profile = profiles.get(user_id_key) or {}
            timezone_name = timezones.get(user_id_key)
            previous = None
            window: deque = deque()
            for txn in items:
                while window and (txn.ts - window[0]).total_seconds() > 3600:
                    window.popleft()
                context = context_from_transaction(
                    txn, previous, txn_count_1h=len(window), timezone_name=timezone_name
                )
                vectors.append(to_vector(build_features(context, profile)))
                window.append(txn.ts)
                previous = txn

        scores = self.score_matrix(np.asarray(vectors, dtype=float)) if self.available else np.zeros(len(vectors))
        for txn, score in zip(transactions, scores):
            txn.anomaly_score = round(float(score), 4)
        db.commit()
        return len(transactions)


# ---------------------------------------------------------------------------
# Instance unique (chargée à la demande)
# ---------------------------------------------------------------------------
detector = AnomalyDetector()
_loaded = False
_store: ArtifactStore | None = None


def get_store() -> ArtifactStore:
    """Stockage d'artefacts configuré (mémoïsé pour la durée du processus)."""
    global _store
    if _store is None:
        _store = build_store()
    return _store


def get_detector() -> AnomalyDetector:
    """
    Charge le modèle depuis le stockage configuré au premier appel.

    Le chargement est unique par processus : aucune lecture disque ou S3 à
    chaque paiement. `reload_detector()` force une relecture après réentraînement.
    """
    global _loaded
    if not _loaded:
        detector.load(get_store())
        _loaded = True
    return detector


def reload_detector() -> AnomalyDetector:
    """Force le rechargement (après un ré-entraînement)."""
    global _loaded
    _loaded = False
    return get_detector()


# ---------------------------------------------------------------------------
# Construction du jeu d'entraînement
# ---------------------------------------------------------------------------
def build_training_matrix(db: Session, include_frauds: bool = False) -> tuple[np.ndarray, list[str]]:
    """
    Construit la matrice d'entraînement à partir de l'historique.

    `include_frauds=False` : on apprend le comportement *normal* uniquement
    (apprentissage non supervisé réaliste : les fraudes passées étiquetées
    servent seulement à mesurer les performances).
    """
    users = list(db.scalars(select(User)).all())
    if not users:
        return np.empty((0, len(FEATURE_NAMES))), []

    profiles = {user.id: build_profile(db, user) for user in users}

    rows: list[list[float]] = []
    emitters: list[str] = []  # identifiant lisible de chaque ligne (débogage)

    for user in users:
        statement = select(Transaction).where(
            Transaction.user_id == user.id,
            Transaction.status != "blocked",
            # Le modèle décrit la dépense : les encaissements (salaire, virements
            # reçus) ne font pas partie du comportement de paiement analysé.
            Transaction.amount < 0,
        )
        if not include_frauds:
            statement = statement.where(Transaction.is_fraud.is_(False))
        items = list(db.scalars(statement.order_by(Transaction.ts.asc())).all())

        profile = profiles[user.id]
        previous = None
        window: deque = deque()
        for txn in items:
            while window and (txn.ts - window[0]).total_seconds() > 3600:
                window.popleft()
            context = context_from_transaction(
                txn, previous, txn_count_1h=len(window), timezone_name=user.timezone
            )
            rows.append(to_vector(build_features(context, profile)))
            emitters.append(f"{user.id}:{txn.id}")
            window.append(txn.ts)
            previous = txn

    return np.asarray(rows, dtype=float), emitters


def train_detector(db: Session, contamination: float | None = None, force: bool = False) -> dict:
    """
    Entraîne (ou réentraîne) le modèle et le sauvegarde dans le stockage configuré.

    Garde-fous anti-emballement (multi-instances, redémarrages en boucle) :

    * **verrou d'entraînement** partagé : si une autre instance entraîne déjà,
      on renvoie ``status="busy"`` sans rien faire (le verrou abandonné expire
      après ``ML_TRAINING_LOCK_TTL_SECONDS``) ;
    * **intervalle minimal** entre deux entraînements
      (``ML_RETRAIN_MIN_INTERVAL_SECONDS``) : un redémarrage rapproché ne
      relance pas l'entraînement ; `force=True` l'ignore explicitement (utilisé
      quand l'artefact est absent ou d'une version incompatible).

    Retourne un rapport : nombre d'exemples, durée, métriques d'évaluation.
    """
    started = time.perf_counter()
    contamination = float(contamination if contamination is not None else settings.ml_contamination)
    store = get_store()
    holder = f"{socket.gethostname()}:{os.getpid()}"

    if not store.acquire_training_lock(settings.ml_training_lock_ttl_seconds, holder):
        return {
            "status": "busy",
            "message": (
                "Un entraînement est déjà en cours sur ce stockage partagé. "
                "Réessayez dans quelques minutes."
            ),
            "n_samples": 0,
            "metrics": {},
            "store": store.describe(),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }

    try:
        if not force:
            age = store.last_write_age_seconds()
            if age is not None and age < settings.ml_retrain_min_interval_seconds:
                return {
                    "status": "skipped",
                    "message": (
                        f"Modèle déjà entraîné il y a {int(age)} s : réentraînement ignoré "
                        f"(intervalle minimal {settings.ml_retrain_min_interval_seconds} s)."
                    ),
                    "n_samples": 0,
                    "metrics": {},
                    "store": store.describe(),
                    "duration_seconds": round(time.perf_counter() - started, 3),
                }

        matrix, _ = build_training_matrix(db)

        if matrix.shape[0] < settings.ml_min_samples:
            return {
                "status": "insufficient_data",
                "message": (
                    f"Données insuffisantes pour l'entraînement : {matrix.shape[0]} opérations "
                    f"(minimum {settings.ml_min_samples})."
                ),
                "n_samples": int(matrix.shape[0]),
                "metrics": {},
                "store": store.describe(),
                "duration_seconds": round(time.perf_counter() - started, 3),
            }

        detector.fit(matrix, contamination)
        detector.save(store)
        reload_detector()
        detector.rescore_transactions(db)

        metrics = evaluate(db)
        return {
            "status": "ok",
            "message": (
                f"Modèle entraîné sur {matrix.shape[0]} opérations normales "
                f"({matrix.shape[1]} variables) et appliqué à l'historique."
            ),
            "n_samples": int(matrix.shape[0]),
            "metrics": metrics,
            "store": store.describe(),
            "feature_version": FEATURE_VERSION,
            "model_version": MODEL_VERSION,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        store.release_training_lock()


# ---------------------------------------------------------------------------
# Score et explication pour une opération
# ---------------------------------------------------------------------------
def score_context(context: dict, profile: dict) -> tuple[float, dict[str, float], list[str]]:
    """
    Évalue une opération : renvoie (probabilité d'anomalie, variables, raisons).

    Les « raisons » sont des phrases françaises destinées à l'utilisateur.
    """
    features = build_features(context, profile)
    score = get_detector().score_features(features)
    return score, features, explain(features, context, profile)


def _format_amount(value: float) -> str:
    return f"{value:,.2f} €".replace(",", " ")


def explain(features: dict[str, float], context: dict, profile: dict) -> list[str]:
    """Traduit les variables en explications compréhensibles."""
    reasons: list[tuple[float, str]] = []
    stats = profile.get("amount_stats") or {}
    amount = float(context.get("amount") or 0.0)

    if features["amount_zscore"] > 2.5:
        median = stats.get("median")
        if median:
            ratio = amount / median if median else 0
            reasons.append(
                (features["amount_zscore"], f"Montant de {_format_amount(amount)} ({ratio:.1f}× votre montant médian de {_format_amount(median)})")
            )
        else:
            reasons.append((features["amount_zscore"], f"Montant de {_format_amount(amount)}, inhabituel"))

    if features["new_device"] >= 1:
        reasons.append((6.0, "Appareil jamais utilisé auparavant"))
    if features["new_location"] >= 1:
        city = context.get("city") or "un nouveau lieu"
        reasons.append((5.0, f"Première opération depuis {city}"))
    if features["geo_velocity"] >= geo.IMPOSSIBLE_SPEED_KMH:
        reasons.append((9.0, f"Déplacement de {features['geo_velocity']:.0f} km/h entre deux paiements : physiquement impossible"))
    elif features["geo_velocity"] >= geo.SUSPICIOUS_SPEED_KMH:
        reasons.append((4.0, f"Déplacement très rapide ({features['geo_velocity']:.0f} km/h) entre deux paiements"))
    if features["txn_count_1h"] >= 3:
        reasons.append((4.0, f"{int(features['txn_count_1h'])} opérations en moins d'une heure"))
    if features["hour_unusual"] >= 0.85:
        reasons.append((3.5, f"Opération à {int(context.get('hour', 0))}h, une heure jamais utilisée"))
    elif features["night_time"] >= 1:
        reasons.append((2.0, "Opération effectuée pendant la nuit (23h-6h)"))
    if features["new_channel"] >= 1:
        reasons.append((2.0, f"Canal de paiement inhabituel ({context.get('channel')})"))

    reasons.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in reasons[:5]] or ["Aucun écart notable par rapport à vos habitudes."]


def _statistical_fallback(features: dict[str, float]) -> float:
    """
    Score de repli si le modèle n'est pas disponible (aucun entraînement).

    Combine l'écart de montant, l'appareil, le lieu et la vélocité avec la
    même échelle que le modèle, afin de garder une protection minimale.
    """
    score = (
        min(features["amount_zscore"] / 25.0, 1.0) * 0.45
        + features["new_device"] * 0.2
        + features["new_location"] * 0.15
        + min(features["geo_velocity"] / geo.IMPOSSIBLE_SPEED_KMH, 1.0) * 0.2
    )
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# Évaluation du modèle
# ---------------------------------------------------------------------------
def _metrics_at(scores: list[float], labels: list[bool], threshold: float) -> dict:
    """Précision, rappel, F1 et matrice de confusion pour un seuil donné."""
    positives = sum(labels)
    true_positive = sum(1 for label, score in zip(labels, scores) if label and score >= threshold)
    false_positive = sum(1 for label, score in zip(labels, scores) if not label and score >= threshold)
    false_negative = positives - true_positive

    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
    recall = true_positive / positives if positives else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "threshold": round(threshold, 2),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": len(labels) - positives - false_positive,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


def evaluate(db: Session, threshold: float = 0.5) -> dict:
    """
    Compare les scores d'anomalie à la vérité terrain simulée (`is_fraud`).

    Deux lectures sont fournies :

    * le seuil opérationnel fixe (0,5 par défaut) ;
    * le seuil qui maximise le F1 sur le jeu de test — utile pour visualiser
      la marge d'amélioration et pour régler le seuil de production.

    Les opérations non étiquetées sont supposées légitimes : simplification
    acceptable pour un prototype (voir README, section « Limites »).
    Les encaissements sont exclus : le modèle porte sur les paiements.
    """
    transactions = list(db.scalars(select(Transaction).where(Transaction.amount < 0)).all())
    if not transactions:
        return {}

    labels = [bool(txn.is_fraud) for txn in transactions]
    scores = [float(txn.anomaly_score or 0.0) for txn in transactions]
    positives = sum(labels)
    if positives == 0:
        return {"n_labeled": len(labels), "n_fraud": 0, "note": "Aucune fraude étiquetée dans le jeu de test."}

    fixed = _metrics_at(scores, labels, threshold)

    # Recherche du seuil maximisant le F1 (grille de 0,20 à 0,95)
    best = fixed
    for step in range(4, 20):
        candidate = _metrics_at(scores, labels, step * 0.05)
        if candidate["f1"] > best["f1"]:
            best = candidate

    auc = _roc_auc(scores, labels)
    return {
        "n_labeled": len(labels),
        "n_fraud": positives,
        "roc_auc": round(auc, 3),
        "estimated_false_positive_rate": round(fixed["false_positive"] / max(1, len(labels) - positives), 4),
        **{key: value for key, value in best.items()},
        "best_threshold": best["threshold"],
        "fixed_threshold": fixed,
    }


def _roc_auc(scores: list[float], labels: list[bool]) -> float:
    """Aire sous la courbe ROC, calculée par la méthode des rangs (Mann-Whitney)."""
    positives = [s for s, l in zip(scores, labels) if l]
    negatives = [s for s, l in zip(scores, labels) if not l]
    if not positives or not negatives:
        return 0.0

    ordered = sorted(zip(scores, labels), key=lambda item: item[0])
    ranks: dict[int, float] = {}
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][0] == ordered[index][0]:
            end += 1
        average_rank = (index + end) / 2 + 1  # rangs à partir de 1
        for position in range(index, end + 1):
            ranks[position] = average_rank
        index = end + 1

    rank_sum = sum(ranks[i] for i, (_, label) in enumerate(ordered) if label)
    n_pos, n_neg = len(positives), len(negatives)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


# ---------------------------------------------------------------------------
# Fiche modèle
# ---------------------------------------------------------------------------
def model_card(db: Session) -> dict:
    """Informations affichées dans l'onglet « Sécurité & IA »."""
    current = get_detector()
    metrics = evaluate(db) if current.available else {}
    metadata = current.metadata or {}
    return {
        "available": current.available,
        "algorithm": metadata.get("algorithm", "IsolationForest (non entraîné)"),
        "trained_at": metadata.get("trained_at"),
        "n_samples": int(metadata.get("n_samples", 0)),
        "n_features": len(FEATURE_NAMES),
        "contamination": float(metadata.get("contamination", settings.ml_contamination)),
        "features": [f"{name} — {FEATURE_LABELS_FR.get(name, '')}" for name in FEATURE_NAMES],
        "metrics": metrics,
        "last_retrain_seconds": metadata.get("fit_seconds"),
        # --- Traçabilité de l'artefact (stockage et versions) --------------
        "store": get_store().describe(),
        "feature_version": FEATURE_VERSION,
        "model_version": metadata.get("model_version", MODEL_VERSION),
        "artifact_format": metadata.get("artifact_format"),
        "load_error": current.load_error,
        "retrain_min_interval_seconds": settings.ml_retrain_min_interval_seconds,
    }


# ---------------------------------------------------------------------------
# Repli statistique (utilisé si le modèle n'est jamais entraîné)
# ---------------------------------------------------------------------------
def quick_statistical_risk(amount: float, profile: dict) -> float:
    """Écart normalisé du montant par rapport à la médiane (0-1)."""
    stats = profile.get("amount_stats") or {}
    median = float(stats.get("median") or 0.0)
    mad = float(stats.get("mad") or 0.0)
    if median <= 0 or profile.get("cold_start"):
        return 0.0
    zscore = (amount - median) / max(1.4826 * mad, 15.0)
    return float(min(1.0, max(0.0, zscore / 12.0)))


__all__ = [
    "AnomalyDetector",
    "detector",
    "get_detector",
    "reload_detector",
    "train_detector",
    "score_context",
    "evaluate",
    "model_card",
    "build_training_matrix",
    "quick_statistical_risk",
]
