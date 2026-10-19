"""
Application FastAPI : assemblage des routeurs, sécurité HTTP et interface.

Démarrage :
    python run.py                # amorçage + serveur (recommandé)
    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from app.config import FRONTEND_DIR, settings
from app.database import SessionLocal
from app.migrations import upgrade_database
from app.models import Transaction, User
from app.routers import auth, dashboard, payments, security_center, transactions

logger = logging.getLogger("nexpay")


# ---------------------------------------------------------------------------
# Cycle de vie
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_: FastAPI):
    """Vérifie la configuration, migre la base, prépare le modèle d'IA."""
    # Une configuration de production dangereuse (clé secrète absente, CORS
    # générique) fait échouer le démarrage : mieux vaut refuser que d'exposer.
    settings.validate_runtime()

    try:
        migration_report = upgrade_database()
        logger.info("Schéma à jour : révision %s", migration_report.get("head_revision"))
    except Exception as error:  # pragma: no cover - le serveur doit démarrer quand même
        logger.warning("Migrations impossibles : %s", error)

    session = SessionLocal()
    try:
        from app.services.otp import purge_expired

        purge_expired(session)

        from app.ml.anomaly import get_detector, train_detector

        detector = get_detector()
        users = int(session.scalar(select(func.count(User.id))) or 0)
        if not detector.available and users > 0:
            # Artefact absent ou version incompatible : le verrou d'entraînement
            # et l'intervalle minimal évitent tout entraînement concurrent ou
            # répété entre les instances redémarrées en boucle.
            result = train_detector(session, force=True)
            if result.get("status") == "ok":
                logger.info("Modèle d'anomalies entraîné au démarrage.")
            else:
                logger.warning("Entraînement non effectué : %s", result.get("message"))
    except Exception as error:  # pragma: no cover - le serveur doit démarrer quand même
        logger.warning("Initialisation du modèle impossible : %s", error)
    finally:
        session.close()

    yield


app = FastAPI(
    title=f"{settings.app_name} — API",
    version=settings.app_version,
    description=(
        "Prototype de paiement électronique sécurisé et d'analyse financière par IA.\n\n"
        "Aucun paiement réel n'est effectué : toutes les opérations sont simulées."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

# ---------------------------------------------------------------------------
# CORS — jamais permissif par défaut
# ---------------------------------------------------------------------------
# En développement, les adresses locales usuelles (frontend 8000, Streamlit
# 8501) sont autorisées si CORS_ORIGINS est vide. En production, une liste vide
# signifie « même origine uniquement » ; « * » est refusé par
# settings.validate_runtime(). L'application ne pose donc jamais de CORS
# générique sans configuration explicite.
if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept-Language"],
    )
    logger.info("CORS autorisé pour : %s", ", ".join(settings.cors_origin_list))
else:
    logger.info("CORS désactivé (même origine uniquement).")

# ---------------------------------------------------------------------------
# Routeurs API
# ---------------------------------------------------------------------------
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(transactions.router)
app.include_router(payments.router)
app.include_router(security_center.router)


# ---------------------------------------------------------------------------
# Gestion d'erreurs en français
# ---------------------------------------------------------------------------
def human_messages(errors: list[dict]) -> str:
    """Transforme les erreurs de validation Pydantic en message lisible."""
    parts: list[str] = []
    for error in errors[:4]:
        location = ".".join(str(item) for item in error.get("loc", ()) if item not in {"body", "query"})
        message = error.get("msg", "valeur invalide")
        parts.append(f"{location or 'requête'} : {message}")
    return "Données invalides — " + " ; ".join(parts)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Renvoie un 422 explicite plutôt que la structure brute de Pydantic."""
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content={"detail": human_messages(exc.errors())})


@app.exception_handler(404)
async def not_found_handler(request: Request, _exc) -> JSONResponse:  # pragma: no cover
    """Les routes inconnues de l'API renvoient du JSON, le reste renvoie l'interface."""
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"detail": "Ressource introuvable."})
    return JSONResponse(status_code=404, content={"detail": "Page introuvable."})


# ---------------------------------------------------------------------------
# Supervision
# ---------------------------------------------------------------------------
@app.get("/api/health", tags=["supervision"])
def health() -> dict:
    """État de santé : base de données, modèle d'IA, schéma, version."""
    from app.database import engine
    from app.i18n import CATALOGS
    from app.migrations import current_revision
    from app.ml.anomaly import get_detector, get_store

    session = SessionLocal()
    try:
        users = int(session.scalar(select(func.count(User.id))) or 0)
        transactions = int(session.scalar(select(func.count(Transaction.id))) or 0)
    finally:
        session.close()

    detector = get_detector()
    return {
        "status": "ok",
        "version": settings.app_version,
        "environment": settings.environment,
        "database": str(settings.resolved_database_url).split("/")[-1],
        "schema_revision": current_revision(engine),
        "users": users,
        "transactions": transactions,
        "model": {
            "disponible": detector.available,
            "entraine_le": detector.metadata.get("trained_at"),
            "exemples": detector.metadata.get("n_samples", 0),
            "contamination": detector.metadata.get("contamination", settings.ml_contamination),
            "version_variables": detector.metadata.get("feature_version"),
            "version_modele": detector.metadata.get("model_version"),
            "stockage": get_store().describe(),
            "erreur_chargement": detector.load_error or None,
        },
        "languages": sorted(CATALOGS.keys()),
        "otp_ttl_seconds": settings.otp_ttl_seconds,
        "seuils_risque": {"accept_max": settings.risk_accept_max, "verify_max": settings.risk_verify_max},
    }


# ---------------------------------------------------------------------------
# Interface web (fichiers statiques)
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Redirige vers la page de connexion."""
    return RedirectResponse(url="/login.html", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


if FRONTEND_DIR.exists():
    # Monté en dernier : les routes /api/* restent prioritaires.
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:  # pragma: no cover
    logger.warning("Dossier frontend introuvable : %s", FRONTEND_DIR)
