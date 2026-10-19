"""
Amorçage de l'application : base de données, données de démonstration, modèle.

Cette logique est partagée par `run.py` (lancement en une commande) et par la
commande `python -m app.bootstrap`.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal, engine, init_db
from app.models import Transaction, User


def ensure_ready(verbose: bool = True, reset: bool = False) -> dict:
    """
    Prépare l'application pour un premier lancement.

    1. crée les tables manquantes ;
    2. génère le jeu de démonstration si la base est vide (ou si `reset`) ;
    3. entraîne le modèle d'anomalies s'il n'existe pas encore.

    Retourne un rapport d'exécution.
    """
    report: dict = {"database": settings.resolved_database_url, "created_data": False, "trained_model": False}

    if verbose:
        print(f"\n{settings.app_name} — préparation de l'environnement")

    init_db()
    session = SessionLocal()
    try:
        users = int(session.scalar(select(func.count(User.id))) or 0)

        if reset or users == 0:
            if reset:
                if verbose:
                    print("  · régénération complète des données de démonstration…")
                from app.seed import seed_database

                report["seed"] = seed_database(session, force=True, verbose=verbose)
            else:
                from app.seed import seed_database

                report["seed"] = seed_database(session, verbose=verbose)
            report["created_data"] = True
            users = int(session.scalar(select(func.count(User.id))) or 0)
        elif verbose:
            transactions = int(session.scalar(select(func.count(Transaction.id))) or 0)
            print(f"  · base existante : {users} utilisateur(s), {transactions} opération(s)")

        # --- Modèle d'anomalies ------------------------------------------
        from app.ml.anomaly import get_detector, train_detector

        detector = get_detector()
        if not detector.available and users > 0:
            if verbose:
                print("  · entraînement du modèle Isolation Forest…")
            result = train_detector(session)
            report["trained_model"] = result["status"] == "ok"
            report["training"] = result
        elif verbose and detector.available:
            metadata = detector.metadata
            print(
                f"  · modèle chargé : {metadata.get('n_samples', 0)} exemples, "
                f"contamination {metadata.get('contamination')}, "
                f"métriques {result_metrics(metadata)}"
            )
    finally:
        session.close()

    report["users"] = users
    report["engine"] = str(engine.url).split("@")[-1]
    return report


def result_metrics(metadata: dict) -> str:
    """Résumé lisible des métadonnées du modèle."""
    return f"{metadata.get('n_features', 0)} variables, entraîné le {metadata.get('trained_at', '?')}"


def main() -> None:
    """Point d'entrée : `python -m app.bootstrap [--reset]`."""
    import argparse

    parser = argparse.ArgumentParser(description="Prépare la base et le modèle NexPay AI.")
    parser.add_argument("--reset", action="store_true", help="Régénère entièrement les données de démonstration.")
    arguments = parser.parse_args()
    ensure_ready(reset=arguments.reset)
    print("\n  ✔ Environnement prêt. Lancez le serveur : python run.py\n")


if __name__ == "__main__":
    main()
