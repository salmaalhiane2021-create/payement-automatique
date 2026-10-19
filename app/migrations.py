"""
Exécution des migrations de schéma, partagée par tous les points d'entrée.

Deux situations sont gérées automatiquement :

1. **Base neuve** : `upgrade head` crée tout le schéma (voir la première
   migration, `migrations/versions/0001_initial.py`).
2. **Base existante sans table `alembic_version`** (base créée avant l'ajout
   des migrations) : elle est « estampillée » au niveau de la révision
   initiale, puis les migrations suivantes s'appliquent. Les données de
   démonstration sont conservées — aucun `DROP`, aucun reset.

Utilisation :

    python -m app.migrations            # mettre à jour la base configurée
    python -m app.migrations --status   # afficher la révision courante
"""

from __future__ import annotations

import argparse

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from app.config import BASE_DIR, settings

ALEMBIC_INI = BASE_DIR / "alembic.ini"
MIGRATIONS_DIR = BASE_DIR / "migrations"

#: Révision correspondant au schéma historique (avant les colonnes régionales).
BASELINE_REVISION = "0001_initial"


def alembic_config(database_url: str | None = None, connection=None) -> Config:
    """Construit la configuration Alembic pointant sur la base demandée."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url or settings.resolved_database_url)
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def current_revision(engine: Engine) -> str | None:
    """Révision actuellement appliquée (None si la table n'existe pas)."""
    if "alembic_version" not in inspect(engine).get_table_names():
        return None
    with engine.connect() as connection:
        return connection.scalar(text("SELECT version_num FROM alembic_version"))


def has_application_tables(engine: Engine) -> bool:
    """La base contient-elle déjà des tables métier (hors `alembic_version`) ?"""
    tables = set(inspect(engine).get_table_names())
    return bool(tables - {"alembic_version"})


def upgrade_database(database_url: str | None = None) -> dict:
    """
    Met la base à jour jusqu'à la dernière révision.

    Retourne un rapport : révision précédente, estampillage éventuel, révision
    finale. Une base existante est estampillée au niveau initial puis migrée
    (aucune donnée n'est supprimée).
    """
    url = database_url or settings.resolved_database_url
    engine = create_engine(url, future=True)
    report: dict = {"status": "ok", "database": url}
    try:
        previous = current_revision(engine)
        report["previous_revision"] = previous
        report["stamped_baseline"] = False

        if previous is None and has_application_tables(engine):
            # Base historique créée par `create_all` : on la marque à la
            # révision initiale, puis `upgrade` applique les migrations
            # suivantes (ajout des colonnes régionales par exemple).
            command.stamp(alembic_config(url), BASELINE_REVISION)
            report["stamped_baseline"] = True
            report["previous_revision"] = BASELINE_REVISION

        command.upgrade(alembic_config(url), "head")

        from alembic.script import ScriptDirectory  # import tardif : léger

        script = ScriptDirectory.from_config(alembic_config(url))
        report["head_revision"] = script.get_current_head()
    finally:
        engine.dispose()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrations de schéma NexPay AI")
    parser.add_argument("--status", action="store_true", help="afficher la révision courante")
    parser.add_argument("--url", default="", help="URL SQLAlchemy cible (défaut : configuration)")
    arguments = parser.parse_args()

    url = arguments.url or settings.resolved_database_url

    if arguments.status:
        engine = create_engine(url, future=True)
        try:
            print(f"Base      : {url}")
            print(f"Révision  : {current_revision(engine) or 'aucune (migrations jamais appliquées)'}")
            print(f"Tables    : {', '.join(sorted(inspect(engine).get_table_names())) or 'aucune'}")
        finally:
            engine.dispose()
        return 0

    report = upgrade_database(url)
    print(f"Base migrée : {report['database']}")
    print(f"Révision précédente : {report['previous_revision'] or 'base neuve'}")
    if report["stamped_baseline"]:
        print(f"Base existante estampillée à la révision initiale ({BASELINE_REVISION}).")
    print(f"Révision finale : {report['head_revision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
