"""
Environnement Alembic : branche les migrations sur la configuration de l'app.

Points d'attention :

* l'URL vient de `DATABASE_URL` (app.config) ou de l'attribut de connexion
  fourni programmatiquement par `app.migrations` (pratique pour les tests) ;
* `render_as_batch=True` sur SQLite : les modifications de colonnes sont
  réécrites en recréation de table (limite du moteur SQLite) ;
* `compare_type=True` : une modification de type est détectée lors des
  autogenerations futures.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Le dossier du projet doit être importable (exécution en ligne de commande).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models  # noqa: E402,F401  (enregistre toutes les tables)
from app.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """URL de migration : argument explicite, sinon configuration applicative."""
    configured = config.get_main_option("sqlalchemy.url")
    return configured or settings.resolved_database_url


def run_migrations_offline() -> None:
    """Génère le SQL sans se connecter (mode `--sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Applique les migrations sur une connexion réelle."""
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as active_connection:
        context.configure(
            connection=active_connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=active_connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
