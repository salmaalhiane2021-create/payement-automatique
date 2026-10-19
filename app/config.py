"""
Configuration centralisée de l'application.

Toutes les valeurs sensibles (clé secrète, seuils, base de données) proviennent
de variables d'environnement ou du fichier `.env`. Aucun secret n'est écrit en
dur dans le code : en développement, si `SECRET_KEY` est absent, une clé est
générée et stockée dans `data/dev_secret.key` (fichier ignoré par git).
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Racine du projet (le dossier qui contient « app/ »)
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR / "frontend"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "ml" / "artifacts"


class Settings(BaseSettings):
    """Paramètres applicatifs, surchargables via `.env` ou l'environnement."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    app_name: str = "NexPay AI"
    app_version: str = "1.0.0"
    environment: str = "development"
    app_timezone: str = "Europe/Paris"

    # --- Sécurité ----------------------------------------------------------
    secret_key: str = ""
    access_token_expire_minutes: int = 120
    jwt_algorithm: str = "HS256"

    # --- Paiement & authentification dynamique -----------------------------
    otp_ttl_seconds: int = 60          # durée de vie d'un code à usage unique
    otp_max_attempts: int = 3          # essais avant invalidation du code
    payment_max_amount: float = 100_000.0
    payment_min_amount: float = 0.5

    # --- Moteur de risque --------------------------------------------------
    risk_accept_max: int = 35          # en dessous : acceptation automatique
    risk_verify_max: int = 70          # entre les deux : vérification renforcée

    # --- Authentification utilisateur --------------------------------------
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    password_min_length: int = 8

    # --- Machine learning --------------------------------------------------
    ml_contamination: float = 0.04
    ml_min_samples: int = 120          # en dessous, on n'entraîne pas le modèle

    # --- Infrastructure ----------------------------------------------------
    database_url: str = ""
    cors_origins: str = "*"

    # ------------------------------------------------------------------
    # Propriétés calculées
    # ------------------------------------------------------------------
    @property
    def resolved_secret_key(self) -> str:
        """Clé de signature : celle de l'environnement, sinon une clé de dev."""
        if self.secret_key:
            return self.secret_key

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        key_file = DATA_DIR / "dev_secret.key"
        if not key_file.exists():
            key_file.write_text(secrets.token_urlsafe(64), encoding="utf-8")
        return key_file.read_text(encoding="utf-8").strip()

    @property
    def resolved_database_url(self) -> str:
        """URL SQLAlchemy ; par défaut un fichier SQLite dans `data/`."""
        if self.database_url:
            return self.database_url
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # .as_posix() évite les antislashs Windows dans l'URL SQLAlchemy
        return f"sqlite:///{(DATA_DIR / 'nexpay.db').as_posix()}"

    @property
    def cors_origin_list(self) -> list[str]:
        """Liste des origines CORS autorisées."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    """Instance unique de configuration (mise en cache)."""
    return Settings()


settings = get_settings()
