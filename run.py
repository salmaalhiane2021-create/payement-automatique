"""
Lancement en une commande.

    python run.py

Le script :
1. valide la configuration d'exécution (refus des réglages dangereux en
   production : clé secrète absente, CORS générique) ;
2. vérifie/installe les prérequis (migrations, données de démo, modèle IA) ;
3. démarre le serveur web ;
4. affiche l'adresse à ouvrir dans le navigateur.

Options :
    python run.py --reset          # régénère les données de démonstration
    python run.py --host 0.0.0.0   # écoute sur toutes les interfaces (conteneur)
    python run.py --port 8080      # change le port
    python run.py --no-browser     # n'ouvre pas le navigateur
    python run.py --production     # désactive le rechargement automatique

Les valeurs par défaut proviennent de la configuration (`HOST`, `PORT`,
`ENVIRONMENT` dans `.env`) : le serveur n'est donc pas limité à la boucle
locale, et la production se configure sans modifier le code.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Le dossier du projet doit être dans le chemin d'import (exécution directe).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.bootstrap import ensure_ready  # noqa: E402
from app.config import settings  # noqa: E402


def open_browser(url: str, delay: float = 1.5) -> None:
    """Ouvre le navigateur après un court délai (le temps que le serveur démarre)."""

    def _open() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover
            pass

    threading.Thread(target=_open, daemon=True).start()


def display_url(host: str, port: int) -> str:
    """Adresse à ouvrir dans le navigateur (`0.0.0.0` -> boucle locale)."""
    friendly = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    return f"http://{friendly}:{port}"


def main() -> None:
    parser = argparse.ArgumentParser(description="NexPay AI — prototype de paiement sécurisé par IA.")
    parser.add_argument("--host", default=settings.host, help=f"Interface d'écoute (défaut : {settings.host})")
    parser.add_argument("--port", type=int, default=settings.port, help=f"Port d'écoute (défaut : {settings.port})")
    parser.add_argument("--reset", action="store_true", help="Régénère les données de démonstration")
    parser.add_argument("--no-browser", action="store_true", help="N'ouvre pas le navigateur")
    parser.add_argument(
        "--production",
        action="store_true",
        help="Mode production : pas de rechargement automatique (voir aussi ENVIRONMENT=production)",
    )
    arguments = parser.parse_args()

    production = arguments.production or settings.is_production

    # Refus explicite des configurations dangereuses en production.
    if arguments.production:
        settings.environment = "production"
    settings.validate_runtime()

    ensure_ready(verbose=True, reset=arguments.reset)

    url = display_url(arguments.host, arguments.port)
    print(f"  · interface web : {url}")
    print(f"  · documentation API : {url}/api/docs")
    print(f"  · compte de démonstration : demo@nexpay.fr / Demo1234!  (code PIN : 4321)\n")
    print("  Appuyez sur Ctrl+C pour arrêter le serveur.\n")

    if not arguments.no_browser:
        open_browser(url)

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=arguments.host,
        port=arguments.port,
        reload=not production,
        reload_dirs=[str(Path(__file__).resolve().parent / "app")],
        log_level="info",
    )


if __name__ == "__main__":
    main()
