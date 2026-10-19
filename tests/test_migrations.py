"""
Tests des migrations de schéma (Alembic).

Points vérifiés
---------------
1. **Base neuve** : `upgrade_database()` crée tout le schéma jusqu'à la
   révision la plus récente, y compris les colonnes régionales.
2. **Base existante sans `alembic_version`** (base créée avant les migrations) :
   elle est estampillée à la révision initiale puis migrée — **aucune donnée
   n'est perdue**, les colonnes existantes sont conservées.
3. **Idempotence** : rejouer l'upgrade ne casse rien et ne repart pas de zéro.

Exécution :
    .venv\\\\Scripts\\\\python -m pytest tests/test_migrations.py -q
"""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.migrations import (
    BASELINE_REVISION,
    alembic_config,
    current_revision,
    upgrade_database,
)

HEAD_REVISION = "0002_international_profile"
INTERNATIONAL_COLUMNS = {"country_code", "locale", "timezone", "currency"}


def sqlite_url(tmp_path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def user_columns(url: str) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        return {column["name"] for column in inspect(engine).get_columns("users")}
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 1. Base neuve
# ---------------------------------------------------------------------------
class TestFreshDatabase:
    def test_upgrade_creates_full_schema(self, tmp_path):
        url = sqlite_url(tmp_path, "fresh.db")
        report = upgrade_database(url)

        assert report["status"] == "ok"
        assert report["previous_revision"] is None      # base neuve
        assert report["stamped_baseline"] is False
        assert report["head_revision"] == HEAD_REVISION

        engine = create_engine(url, future=True)
        try:
            tables = set(inspect(engine).get_table_names())
            assert "users" in tables and "transactions" in tables
            assert current_revision(engine) == HEAD_REVISION
        finally:
            engine.dispose()

        assert INTERNATIONAL_COLUMNS <= user_columns(url)

    def test_upgrade_is_idempotent(self, tmp_path):
        url = sqlite_url(tmp_path, "idem.db")
        upgrade_database(url)
        second = upgrade_database(url)
        assert second["previous_revision"] == HEAD_REVISION
        assert second["stamped_baseline"] is False
        assert second["head_revision"] == HEAD_REVISION


# ---------------------------------------------------------------------------
# 2. Base historique : estampillage automatique, données conservées
# ---------------------------------------------------------------------------
class TestExistingDatabaseWithoutAlembic:
    def _build_legacy_database(self, url: str) -> None:
        """Reproduit une base créée par l'ancien `create_all` (schéma initial)."""
        # Le schéma initial est celui de la première migration.
        command.upgrade(alembic_config(url), BASELINE_REVISION)

        engine = create_engine(url, future=True)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO users (
                            id, full_name, email, password_hash, pin_hash, phone, city,
                            country, monthly_income, risk_aversion, failed_attempts,
                            locked_until, last_login_at, is_active, created_at
                        ) VALUES (
                            1, 'Client Historique', 'historique@example.com', 'empreinte',
                            NULL, '+33600000000', 'Paris', 'France', 2500.0, 0.5, 0,
                            NULL, NULL, 1, '2026-01-01 00:00:00'
                        )
                        """
                    )
                )
                # Une base historique n'a jamais eu de table de suivi Alembic.
                connection.execute(text("DROP TABLE alembic_version"))
        finally:
            engine.dispose()

    def test_legacy_database_is_stamped_then_migrated_without_data_loss(self, tmp_path):
        url = sqlite_url(tmp_path, "legacy.db")
        self._build_legacy_database(url)

        # Avant migration : aucune révision, colonnes régionales absentes.
        engine = create_engine(url, future=True)
        try:
            assert current_revision(engine) is None
        finally:
            engine.dispose()
        assert not (INTERNATIONAL_COLUMNS & user_columns(url))

        report = upgrade_database(url)
        assert report["stamped_baseline"] is True
        assert report["previous_revision"] == BASELINE_REVISION
        assert report["head_revision"] == HEAD_REVISION

        # La ligne existante est intacte et les nouvelles colonnes sont
        # renseignées par la valeur par défaut (FR / fr-FR / Europe/Paris / EUR).
        engine = create_engine(url, future=True)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT email, city, country, country_code, locale, timezone, currency "
                        "FROM users WHERE id = 1"
                    )
                ).one()
                assert row.email == "historique@example.com"
                assert row.city == "Paris" and row.country == "France"
                assert (row.country_code, row.locale, row.timezone, row.currency) == (
                    "FR",
                    "fr-FR",
                    "Europe/Paris",
                    "EUR",
                )
            assert current_revision(engine) == HEAD_REVISION
            assert INTERNATIONAL_COLUMNS <= {c["name"] for c in inspect(engine).get_columns("users")}
        finally:
            engine.dispose()

        # Rejouer l'upgrade reste sans danger.
        again = upgrade_database(url)
        assert again["previous_revision"] == HEAD_REVISION
        assert again["stamped_baseline"] is False

    def test_empty_database_is_not_stamped(self, tmp_path):
        url = sqlite_url(tmp_path, "empty.db")
        report = upgrade_database(url)
        assert report["stamped_baseline"] is False
        assert report["previous_revision"] is None


# ---------------------------------------------------------------------------
# 3. Cohérence du schéma migré avec les modèles
# ---------------------------------------------------------------------------
def test_migrated_schema_matches_models(tmp_path):
    """Aucune divergence entre les modèles SQLAlchemy et le schéma migré."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.database import Base

    url = sqlite_url(tmp_path, "compare.db")
    upgrade_database(url)

    engine = create_engine(url, future=True)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            differences = compare_metadata(context, Base.metadata)
        # Les différences de type SQLite (Float/DateTime) sont tolérées ; toute
        # table ou colonne manquante doit en revanche faire échouer le test.
        structural = [
            entry
            for entry in differences
            if isinstance(entry, tuple) and entry and entry[0] in {"add_table", "remove_table", "add_column", "remove_column"}
        ]
        assert structural == [], structural
    finally:
        engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
