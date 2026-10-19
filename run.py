"""
Lancement en une commande.

    python run.py

Le script :
1. vérifie/installe les prérequis (base de données, données de démo, modèle IA) ;
2. démarre le serveur web ;
3. affiche l'adresse à ouvrir dans le navigateur.

Options :
    python run.py --reset          # régénère les données de démonstration
    python run.py --port 8080      # change le port
    python run.py --no-browser     # n'ouvre pas le navigateur
    python run.py --production     # désactive le rechargement automatique
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


def open_browser(url: str, delay: float = 1.5) -> None:
    """Ouvre le navigateur après un court délai (le temps que le serveur démarre)."""

    def _open() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover
            pass

    threading.Thread(target=_open, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="NexPay AI — prototype de paiement sécurisé par IA.")
    parser.add_argument("--host", default="127.0.0.1", help="Interface d'écoute (défaut : 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port d'écoute (défaut : 8000)")
    parser.add_argument("--reset", action="store_true", help="Régénère les données de démonstration")
    parser.add_argument("--no-browser", action="store_true", help="N'ouvre pas le navigateur")
    parser.add_argument("--production", action="store_true", help="Désactive le rechargement automatique")
    arguments = parser.parse_args()

    ensure_ready(verbose=True, reset=arguments.reset)

    url = f"http://{'127.0.0.1' if arguments.host in {'0.0.0.0', '127.0.0.1'} else arguments.host}:{arguments.port}"
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
        reload=not arguments.production,
        reload_dirs=[str(Path(__file__).resolve().parent / "app")],
        log_level="info",
    )


if __name__ == "__main__":
    main()
