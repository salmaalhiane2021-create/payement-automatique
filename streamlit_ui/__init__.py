"""
Interface Streamlit de NexPay AI — modules d'affichage.

Règle d'architecture (identique à l'API FastAPI) : **aucune règle métier ici**.
Les pages appellent les services partagés de `app/services/` et ne font que
présenter les résultats (mise en forme localisée, composants HTML, navigation).

Organisation
------------
* `context.py`   : session, utilisateur connecté, langue, locale, fuseau, devise ;
* `formatting.py`: montants, pourcentages, dates et libellés localisés ;
* `components.py`: briques visuelles (hero, badges, indicateurs) ;
* `styles.py`    : feuille de style de l'application ;
* `login.py`     : écran de connexion (service d'authentification partagé) ;
* `shell.py`     : coquille applicative (barre latérale, navigation, région) ;
* `pages/`       : une page par domaine fonctionnel.

Le point d'entrée reste `streamlit_app.py` (bootstrap + configuration de page).
"""

from __future__ import annotations

from streamlit_ui.main import run

__all__ = ["run"]
