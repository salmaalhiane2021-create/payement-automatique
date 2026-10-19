"""
Modèles ORM (tables de la base de données).

Vue d'ensemble :

    User ──┬── Account          (comptes courant / épargne)
           ├── Device           (appareils de confiance)
           ├── Transaction      (historique financier)
           ├── PaymentChallenge (code OTP à usage unique, 60 s)
           ├── BehavioralProfile(profil comportemental calculé par l'IA)
           ├── Alert            (alertes de fraude / sécurité)
           └── AuditLog         (journal de sécurité)

Note : les montants sont stockés en Float et arrondis à 2 décimales dans le
code métier. C'est acceptable pour un prototype ; en production on utiliserait
un type décimal (NUMERIC) pour éviter toute erreur d'arrondi.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, now_utc


# ---------------------------------------------------------------------------
# Utilisateurs et comptes
# ---------------------------------------------------------------------------
class User(Base):
    """Utilisateur de l'application bancaire."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True, nullable=False)

    # Mots de passe : jamais en clair -> empreinte bcrypt
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Code PIN (haché également) : utilisé comme vérification supplémentaire
    # lorsque le moteur de risque exige une authentification renforcée.
    pin_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    phone: Mapped[str] = mapped_column(String(30), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    country: Mapped[str] = mapped_column(String(80), default="France")
    monthly_income: Mapped[float] = mapped_column(Float, default=0.0)
    risk_aversion: Mapped[float] = mapped_column(Float, default=0.5)  # 0 = téméraire, 1 = prudent

    # Anti-force brute : compteur d'échecs + verrouillage temporaire
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

    accounts: Mapped[list["Account"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    devices: Mapped[list["Device"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.email}>"


class Account(Base):
    """Compte bancaire (courant ou épargne)."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="courant")  # courant | epargne
    label: Mapped[str] = mapped_column(String(80), default="Compte courant")
    iban_masked: Mapped[str] = mapped_column(String(40), default="FR76 •••• •••• 0000")
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

    user: Mapped[User] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")


class Device(Base):
    """Appareil connu de l'utilisateur (empreinte technique)."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(80), default="Appareil inconnu")
    platform: Mapped[str] = mapped_column(String(40), default="")
    is_trusted: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now_utc)

    user: Mapped[User] = relationship(back_populates="devices")


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
class Transaction(Base):
    """Opération financière (débit négatif, crédit positif)."""

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_user_ts", "user_id", "ts"),
        Index("ix_transactions_user_category", "user_id", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)

    ts: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)  # UTC
    amount: Mapped[float] = mapped_column(Float, nullable=False)                 # signé
    currency: Mapped[str] = mapped_column(String(3), default="EUR")

    label: Mapped[str] = mapped_column(String(140), default="")        # libellé affiché
    merchant: Mapped[str] = mapped_column(String(120), default="")     # commerçant / bénéficiaire
    category: Mapped[str] = mapped_column(String(40), default="Autres")
    channel: Mapped[str] = mapped_column(String(20), default="carte")  # carte | virement | atm | en_ligne | prelevement

    city: Mapped[str] = mapped_column(String(80), default="")
    country: Mapped[str] = mapped_column(String(80), default="France")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    device_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(45), default="")

    # --- Résultats de l'analyse IA ----------------------------------------
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)      # 0-100 (règles + IA)
    anomaly_score: Mapped[float] = mapped_column(Float, default=0.0)   # 0-1 (Isolation Forest)
    decision: Mapped[str] = mapped_column(String(20), default="accept")  # accept | verify | block
    status: Mapped[str] = mapped_column(String(20), default="completed") # completed | blocked | pending

    # --- Étiquettes (jeu de test) -----------------------------------------
    is_fraud: Mapped[bool] = mapped_column(Boolean, default=False)     # vérité terrain simulée
    fraud_type: Mapped[str | None] = mapped_column(String(60), nullable=True)

    meta: Mapped[dict] = mapped_column(JSON, default=dict)             # explications, facteurs…

    user: Mapped[User] = relationship(back_populates="transactions")
    account: Mapped[Account | None] = relationship(back_populates="transactions")


# ---------------------------------------------------------------------------
# Authentification dynamique (OTP)
# ---------------------------------------------------------------------------
class PaymentChallenge(Base):
    """
    Défi d'authentification lié à UNE transaction.

    Le code à 6 chiffres n'est jamais stocké en clair : seule une empreinte
    HMAC-SHA256 (clé = SECRET_KEY) est conservée. Le défi expire au bout de
    `OTP_TTL_SECONDS` (60 s) et devient inutilisable après consommation ou
    après `OTP_MAX_ATTEMPTS` essais infructueux.
    """

    __tablename__ = "payment_challenges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), default="paiement")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)   # montant, marchand, ville…

    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    decision: Mapped[str] = mapped_column(String(20), default="accept")
    require_pin: Mapped[bool] = mapped_column(Boolean, default=False)  # 2e facteur (vérification renforcée)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None


# ---------------------------------------------------------------------------
# Intelligence : profil comportemental, alertes, journal
# ---------------------------------------------------------------------------
class BehavioralProfile(Base):
    """Instantané du profil comportemental (recalculé à chaque transaction)."""

    __tablename__ = "behavioral_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Alert(Base):
    """Alerte de fraude, anomalie ou recommandation de sécurité."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id"), nullable=True)

    kind: Mapped[str] = mapped_column(String(20), default="anomalie")   # fraude | anomalie | securite | info
    severity: Mapped[str] = mapped_column(String(20), default="moyen")  # critique | eleve | moyen | faible
    title: Mapped[str] = mapped_column(String(140), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="alerts")


class AuditLog(Base):
    """Journal d'audit : connexions, paiements, décisions de sécurité."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok | echec | bloque
    detail: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str] = mapped_column(String(45), default="")
    user_agent: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
