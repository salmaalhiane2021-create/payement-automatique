"""
Configuration centralisée de l'application.

Toutes les valeurs sensibles (clé secrète, seuils, base de données) proviennent
de variables d'environnement ou du fichier `.env`. Aucun secret n'est écrit en
dur dans le code : en développement, si `SECRET_KEY` est absent, une clé est
générée et stockée dans `data/dev_secret.key` (fichier ignoré par git).

Déploiement
-----------
* `HOST` / `PORT` : interface et port d'écoute (mettez `0.0.0.0` derrière un
  reverse-proxy ou dans un conteneur).
* `CORS_ORIGINS` : liste d'origines autorisées, séparées par des virgules.
  Vide en développement => origines locales ; en production, vide signifie
  « même origine uniquement » et `*` est refusé par `validate_runtime()`.
* `SECRET_KEY` : obligatoire en production (64 caractères aléatoires minimum
  recommandés) ; la clé de développement n'est jamais utilisée si
  `ENVIRONMENT=production`.
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
    app_version: str = "1.1.0"
    environment: str = "development"
    app_timezone: str = "Europe/Paris"
    default_locale: str = "fr-FR"
    default_currency: str = "EUR"

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
    # Emplacement des artefacts, partageable entre plusieurs instances :
    #   file:///chemin/absolu   (défaut : app/ml/artifacts)
    #   s3://bucket/prefix      (nécessite le paquet optionnel boto3)
    model_store_uri: str = ""
    # Délai minimal entre deux réentraînements (anti-emballement).
    ml_retrain_min_interval_seconds: int = 300
    # Durée de vie du verrou d'entraînement (libéré automatiquement après).
    ml_training_lock_ttl_seconds: int = 900

    # --- Devises -----------------------------------------------------------
    # Conversion désactivée par défaut. Pour l'activer, fournissez une source
    # fiable de taux (JSON « devise_quote: taux_pour_1_EUR »).
    # Exemple : FX_RATES_JSON={"USD": 1.08, "GBP": 0.85}
    fx_rates_json: str = ""
    fx_rates_source: str = ""          # libellé documentaire de la source des taux

    # --- Infrastructure ----------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    database_url: str = ""
    cors_origins: str = ""             # vide : valeurs de dev en développement

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
    def resolved_model_store_uri(self) -> str:
        """URI du stockage des artefacts du modèle (fichier local par défaut)."""
        if self.model_store_uri:
            return self.model_store_uri
        return f"file://{ARTIFACTS_DIR.as_posix()}"

    @property
    def cors_origin_list(self) -> list[str]:
        """
        Origines CORS autorisées.

        En développement, une liste vide retombe sur les adresses locales
        usuelles (FastAPI 8000 / Streamlit 8501). En production, une liste vide
        signifie « même origine uniquement » : aucun CORS permissif par défaut.
        """
        configured = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if configured:
            return configured
        if self.is_production:
            return []
        return [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:8501",
            "http://127.0.0.1:8501",
        ]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    # ------------------------------------------------------------------
    # Validation d'exécution
    # ------------------------------------------------------------------
    def validate_runtime(self) -> None:
        """
        Refuse les configurations dangereuses en production.

        Appelée au démarrage du serveur, du bootstrap et de Streamlit :
        une clé secrète explicite est exigée et le CORS generique est refusé.
        En développement, aucune contrainte (confort de la démo).
        """
        if not self.is_production:
            return

        if not self.secret_key or len(self.secret_key) < 32:
            raise RuntimeError(
                "SECRET_KEY est obligatoire en production (32 caractères minimum). "
                "Générez-la avec : python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        if "*" in self.cors_origin_list:
            raise RuntimeError(
                "CORS_ORIGINS ne peut pas contenir « * » en production. "
                "Indiquez explicitement les origines autorisées, ou laissez vide "
                "pour n'autoriser que la même origine."
            )


@lru_cache
def get_settings() -> Settings:
    """Instance unique de configuration (mise en cache)."""
    return Settings()


settings = get_settings()
