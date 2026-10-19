"""Point d'entrée de l'interface Streamlit (bootstrap puis aiguillage)."""

from __future__ import annotations

import streamlit as st

from app.bootstrap import ensure_ready
from app.i18n import normalize_language, t

from streamlit_ui.context import build_context, current_user, db_session
from streamlit_ui.login import render_login
from streamlit_ui.shell import render_shell
from streamlit_ui.styles import apply_style


@st.cache_resource(show_spinner=False)
def _bootstrap() -> dict:
    """Crée la base, applique les migrations et prépare le modèle (une fois par processus)."""
    return ensure_ready(verbose=False)


def run() -> None:
    """Prépare l'environnement puis affiche la connexion ou l'application."""
    apply_style()

    lang = normalize_language(st.session_state.get("ui_lang") or "fr")
    with st.spinner(t("common.loading", lang)):
        _bootstrap()

    with db_session() as db:
        user = current_user(db)
        if user is None:
            render_login(db)
            return
        render_shell(build_context(db, user))


__all__ = ["run"]
