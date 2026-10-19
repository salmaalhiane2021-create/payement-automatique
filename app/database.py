"""
Connexion à la base de données (SQLAlchemy 2.x).

Le prototype utilise SQLite : aucun serveur à installer, le fichier
`data/nexpay.db` est créé automatiquement. Pour passer à PostgreSQL, il suffit
de définir la variable d'environnement `DATABASE_URL` (ex.
`postgresql+psycopg://user:pass@localhost/nexpay`).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# ---------------------------------------------------------------------------
# Moteur et fabrique de sessions
# ---------------------------------------------------------------------------
_connect_args = {"check_same_thread": False} if settings.resolved_database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.resolved_database_url,
    connect_args=_connect_args,
    echo=False,
    future=True,
)

# SQLite : activer les clés étrangères (désactivées par défaut) et le mode WAL
# (meilleure concurrence lecture/écriture pour une démo multi-onglets).
if settings.resolved_database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    """Classe de base de tous les modèles ORM."""


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------
def get_db() -> Generator[Session, None, None]:
    """Dépendance FastAPI : fournit une session par requête puis la referme."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crée les tables manquantes (idempotent)."""
    from app import models  # noqa: F401  (import nécessaire pour enregistrer les tables)

    Base.metadata.create_all(bind=engine)


def now_utc() -> datetime:
    """
    Horodatage UTC « naïf ».

    SQLite ne conserve pas le décalage horaire : on stocke systématiquement en
    UTC et on convertit vers le fuseau de l'utilisateur à l'affichage.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
