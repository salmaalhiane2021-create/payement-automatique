"""
NexPay AI — interface Streamlit (démonstration en ligne).

Ce fichier n'est qu'un **point d'entrée** : configuration de la page puis
`streamlit_ui.run()`. Toute l'interface vit dans le paquet `streamlit_ui/`
(navigation, pages, mise en forme localisée) et toutes les règles métier dans
`app/services/` — partagées avec l'API FastAPI, jamais dupliquées ici.

Lancement local
---------------
    streamlit run streamlit_app.py

Déploiement Streamlit Community Cloud
-------------------------------------
    Main file path : streamlit_app.py

La base SQLite, les migrations (Alembic) et le modèle sont préparés
automatiquement au premier lancement (voir `app/bootstrap.py`). Sur Streamlit
Cloud, le disque est éphémère : les données de démonstration sont régénérées à
chaque redémarrage de l'application.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="NexPay AI — Banque & paiement sécurisé",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from streamlit_ui import run  # noqa: E402  (doit suivre set_page_config)

run()
