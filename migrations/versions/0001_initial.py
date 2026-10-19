"""Schéma initial — révision de référence (baseline).

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-19

Rôle de cette révision
----------------------
Ce fichier décrit le schéma **historique** de NexPay AI (tables `users`,
`accounts`, `transactions`, `payment_challenges`, `devices`,
`behavioral_profiles`, `alerts`, `audit_logs`), c'est-à-dire l'état de la base
créée par `Base.metadata.create_all` avant l'introduction des migrations.

Pourquoi c'est important
------------------------
Une base de démonstration existante ne possède pas de table `alembic_version`.
`app/migrations.py` la détecte, l'« estampille » à cette révision (aucune table
n'est recréée, aucune donnée n'est supprimée), puis applique les migrations
suivantes — la migration `0002_international_profile` ajoute les colonnes
régionales. Une base neuve part directement de cette révision.

Convention
----------
Les noms de révision sont lisibles et stables (`0001_initial`,
`0002_international_profile`) plutôt qu'aléatoires : l'historique est ainsi
facile à suivre dans les journaux de déploiement.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=180), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("pin_hash", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("country", sa.String(length=80), nullable=False),
        sa.Column("monthly_income", sa.Float(), nullable=False),
        sa.Column("risk_aversion", sa.Float(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("iban_masked", sa.String(length=40), nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_accounts_user_id"), ["user_id"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("user_agent", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_logs_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_created_at"), ["created_at"], unique=False)

    op.create_table(
        "behavioral_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("behavioral_profiles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_behavioral_profiles_user_id"), ["user_id"], unique=True)

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("is_trusted", sa.Boolean(), nullable=False),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_devices_fingerprint"), ["fingerprint"], unique=False)
        batch_op.create_index(batch_op.f("ix_devices_user_id"), ["user_id"], unique=False)

    op.create_table(
        "payment_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("require_pin", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("payment_challenges", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_payment_challenges_user_id"), ["user_id"], unique=False)

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("label", sa.String(length=140), nullable=False),
        sa.Column("merchant", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("country", sa.String(length=80), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("device_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("anomaly_score", sa.Float(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("is_fraud", sa.Boolean(), nullable=False),
        sa.Column("fraud_type", sa.String(length=60), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_transactions_ts"), ["ts"], unique=False)
        batch_op.create_index("ix_transactions_user_category", ["user_id", "category"], unique=False)
        batch_op.create_index(batch_op.f("ix_transactions_user_id"), ["user_id"], unique=False)
        batch_op.create_index("ix_transactions_user_ts", ["user_id", "ts"], unique=False)

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=140), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_alerts_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_alerts_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_alerts_user_id"))
        batch_op.drop_index(batch_op.f("ix_alerts_created_at"))
    op.drop_table("alerts")

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_index("ix_transactions_user_ts")
        batch_op.drop_index(batch_op.f("ix_transactions_user_id"))
        batch_op.drop_index("ix_transactions_user_category")
        batch_op.drop_index(batch_op.f("ix_transactions_ts"))
    op.drop_table("transactions")

    with op.batch_alter_table("payment_challenges", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_payment_challenges_user_id"))
    op.drop_table("payment_challenges")

    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_devices_user_id"))
        batch_op.drop_index(batch_op.f("ix_devices_fingerprint"))
    op.drop_table("devices")

    with op.batch_alter_table("behavioral_profiles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_behavioral_profiles_user_id"))
    op.drop_table("behavioral_profiles")

    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_logs_created_at"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_action"))
    op.drop_table("audit_logs")

    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_accounts_user_id"))
    op.drop_table("accounts")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_email"))
    op.drop_table("users")
