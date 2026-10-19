"""
Schémas Pydantic : validation stricte des entrées et formatage des sorties.

Toute donnée entrante est contrôlée ici (types, bornes, longueurs, formats)
avant d'atteindre la logique métier.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import settings

# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------
EMAIL_RE = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"


class RegisterRequest(BaseModel):
    """
    Création de compte.

    Aucune nationalité n'est demandée ni stockée. Les champs régionaux sont
    facultatifs : `country_code`, `locale`, `timezone` et `currency` sont
    validés par le service d'authentification puis utilisés pour l'affichage.
    """

    full_name: str = Field(min_length=3, max_length=120)
    email: str = Field(pattern=EMAIL_RE, max_length=180)
    password: str = Field(min_length=8, max_length=72)
    pin: str = Field(default="1234", min_length=4, max_length=6)
    phone: str = Field(default="", max_length=30)
    city: str = Field(default="", max_length=80)
    country: str = Field(default="", max_length=80)
    country_code: str = Field(default="", max_length=2)
    locale: str = Field(default="", max_length=15)
    timezone: str = Field(default="", max_length=50)
    currency: str = Field(default="", max_length=3)

    @field_validator("full_name", "city", "phone", "country", "country_code", "locale", "timezone", "currency")
    @classmethod
    def _strip(cls, value: str) -> str:
        return (value or "").strip()


class UpdateProfileRequest(BaseModel):
    """Mise à jour des préférences régionales et coordonnées (facultatif)."""

    city: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, max_length=80)
    country_code: str | None = Field(default=None, max_length=2)
    phone: str | None = Field(default=None, max_length=30)
    locale: str | None = Field(default=None, max_length=15)
    timezone: str | None = Field(default=None, max_length=50)
    currency: str | None = Field(default=None, max_length=3)

    @field_validator("city", "phone", "country", "country_code", "locale", "timezone", "currency")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class LoginRequest(BaseModel):
    """Connexion."""

    email: str = Field(pattern=EMAIL_RE, max_length=180)
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    """Utilisateur renvoyé au client (jamais d'empreinte de mot de passe)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    phone: str
    city: str
    country: str
    country_code: str
    locale: str
    timezone: str
    currency: str
    monthly_income: float


class LoginResponse(BaseModel):
    """Réponse d'une connexion réussie."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# ---------------------------------------------------------------------------
# Paiement sécurisé
# ---------------------------------------------------------------------------
class PaymentQuoteRequest(BaseModel):
    """
    Demande d'analyse de risque avant paiement.

    Aucune donnée de carte réelle n'est demandée : la simulation se contente du
    marchand, du montant, du contexte (lieu, appareil, canal).

    `simulated_hour` n'existe que pour la démonstration : il permet de tester le
    comportement du moteur à une heure de la journée choisie (par exemple 3 h du
    matin) sans attendre cette heure réelle. `simulated_minute` complète ce
    réglage (utile pour enchaîner deux opérations à quelques minutes d'écart).
    """

    merchant: str = Field(min_length=2, max_length=120)
    amount: float = Field(gt=0, le=settings.payment_max_amount)
    category: str = Field(default="Shopping", max_length=40)
    # Aucune localisation n'est imposée : si la ville est inconnue du
    # référentiel géographique, le facteur « zone » n'est simplement pas évalué.
    city: str = Field(default="", max_length=80)
    country: str = Field(default="", max_length=80)
    channel: Literal["carte", "virement", "atm", "en_ligne", "prelevement"] = "en_ligne"
    device_fingerprint: str | None = Field(default=None, max_length=64)
    device_label: str = Field(default="Appareil actuel", max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    simulated_hour: int | None = Field(default=None, ge=0, le=23)
    simulated_minute: int | None = Field(default=None, ge=0, le=59)
    note: str = Field(default="", max_length=200)

    @field_validator("merchant", "city", "country", "category", "device_label")
    @classmethod
    def _strip(cls, value: str) -> str:
        return (value or "").strip()


class RiskFactor(BaseModel):
    """Facteur de risque expliqué en langage naturel."""

    code: str
    label: str
    detail: str
    weight: float          # points ajoutés au score final
    severity: Literal["info", "faible", "moyen", "eleve", "critique"]


class PaymentQuoteResponse(BaseModel):
    """Résultat de l'analyse de risque."""

    decision: Literal["accept", "verify", "block"]
    decision_label: str
    risk_score: float
    anomaly_score: float
    factors: list[RiskFactor]
    summary: str
    challenge_id: int | None = None
    require_pin: bool = False
    otp_ttl_seconds: int = settings.otp_ttl_seconds
    # Uniquement en mode simulation : le code est renvoyé pour la démonstration,
    # il serait en réalité envoyé par SMS. Voir README (« Limites du prototype »).
    simulation_code: str | None = None
    masked_phone: str = ""


class PaymentConfirmRequest(BaseModel):
    """Validation du paiement par le code à usage unique."""

    challenge_id: int
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    pin: str | None = Field(default=None, max_length=6, pattern=r"^\d{4,6}$")


class ChallengeStatusOut(BaseModel):
    """État d'un défi d'authentification (compte à rebours côté client)."""

    challenge_id: int
    expires_at: datetime
    seconds_remaining: int
    attempts_left: int
    consumed: bool
    expired: bool
    decision: str
    require_pin: bool
    amount: float
    merchant: str


class PaymentResultOut(BaseModel):
    """Résultat final d'un paiement."""

    status: Literal["completed", "blocked"]
    message: str
    transaction_id: int | None = None
    risk_score: float = 0.0
    anomaly_score: float = 0.0
    new_balance: float | None = None
    alert_id: int | None = None


# ---------------------------------------------------------------------------
# Transactions et tableau de bord
# ---------------------------------------------------------------------------
class TransactionOut(BaseModel):
    """Ligne d'historique."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    amount: float
    currency: str
    label: str
    merchant: str
    category: str
    channel: str
    city: str
    country: str
    risk_score: float
    anomaly_score: float
    decision: str
    status: str
    is_fraud: bool


class CategoryTotal(BaseModel):
    """Total des dépenses par catégorie."""

    category: str
    total: float
    count: int
    share: float


class DailyTotal(BaseModel):
    """Total des dépenses pour une journée."""

    date: str
    debit: float
    credit: float


class DashboardOut(BaseModel):
    """Données agrégées du tableau de bord."""

    user: UserOut
    checking_balance: float
    savings_balance: float
    month_spending: float
    month_income: float
    month_net: float
    balance_history: list[float]
    categories: list[CategoryTotal]
    daily: list[DailyTotal]
    subscriptions_monthly: float
    alerts_unread: int
    recent_transactions: list[TransactionOut]
    recent_alerts: list[dict]


# ---------------------------------------------------------------------------
# Centre de sécurité et IA
# ---------------------------------------------------------------------------
class AlertOut(BaseModel):
    """Alerte affichée dans l'interface."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    severity: str
    title: str
    message: str
    details: dict
    created_at: datetime
    read_at: datetime | None


class RecommendationOut(BaseModel):
    """Recommandation personnalisée."""

    code: str
    kind: Literal["securite", "epargne", "budget"]
    title: str
    detail: str
    priority: int
    impact_eur: float | None = None
    action: str = ""


class AnomalyOut(BaseModel):
    """Transaction considérée comme anormale par le modèle."""

    transaction: TransactionOut
    anomaly_score: float
    risk_score: float
    reasons: list[str]


class BehavioralProfileOut(BaseModel):
    """Profil comportemental synthétique."""

    updated_at: datetime
    total_transactions: int
    period_days: int
    average_amount: float
    median_amount: float
    typical_range: list[float]
    favorite_hour: int
    hourly_distribution: list[float]
    weekday_distribution: list[float]
    known_cities: list[str]
    known_devices: list[str]
    known_channels: list[str]
    top_categories: list[dict]
    top_merchants: list[dict]
    monthly_average_spending: float
    subscriptions: list[dict]
    night_ratio: float
    online_ratio: float
    insights: list[str]


class ModelCardOut(BaseModel):
    """Fiche descriptive du modèle de détection d'anomalies."""

    available: bool
    algorithm: str
    trained_at: str | None
    n_samples: int
    n_features: int
    contamination: float
    features: list[str]
    metrics: dict
    decision_metrics: dict = {}
    last_retrain_seconds: float | None


class RetrainOut(BaseModel):
    """Résultat d'un ré-entraînement à la demande."""

    status: str
    message: str
    n_samples: int
    duration_seconds: float
    metrics: dict
