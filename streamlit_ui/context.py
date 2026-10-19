"""
Contexte d'affichage partagé par toutes les pages Streamlit.

Le contexte rassemble la session SQLAlchemy, l'utilisateur connecté et ses
préférences régionales (langue d'interface, locale, fuseau, devise). Les pages
ne manipulent que ce contexte : la langue et les formats ne sont jamais codés
en dur dans une page.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import streamlit as st

from app.config import settings
from app.database import SessionLocal
from app.i18n import CATALOGS, DEFAULT_LANGUAGE, LANGUAGE_LABELS, normalize_language, t

if TYPE_CHECKING:  # pragma: no cover - typage uniquement
    from sqlalchemy.orm import Session

    from app.models import User


@contextmanager
def db_session():
    """Fournit une session SQLAlchemy propre à chaque exécution du script."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_user(db: "Session") -> "User | None":
    """Utilisateur connecté (session Streamlit) ; déconnexion si inactif."""
    user_id = st.session_state.get("user_id")
    if not user_id:
        return None
    from app.models import User

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        st.session_state.pop("user_id", None)
        return None
    return user


def language_selector(current: str, key: str = "ui_lang", label: str = "Langue") -> str:
    """
    Sélecteur de langue d'interface (fr/en), mémorisé dans la session.

    La langue est une préférence d'affichage : elle ne modifie pas les données
    de l'utilisateur (la locale enregistrée, elle, se règle dans « Profil & région »).
    """
    codes = list(LANGUAGE_LABELS)
    index = codes.index(current) if current in codes else 0
    choice = st.selectbox(
        label,
        codes,
        index=index,
        format_func=lambda code: LANGUAGE_LABELS.get(code, code),
        key=f"{key}_select",
        label_visibility="collapsed",
    )
    normalized = normalize_language(choice)
    if normalized != current:
        # La langue change : le contexte de cette exécution a déjà été construit,
        # on relance immédiatement pour réafficher toute l'interface traduite.
        st.session_state[key] = normalized
        st.rerun()
    st.session_state[key] = normalized
    return normalized


@dataclass
class AppContext:
    """Contexte d'une page : session, utilisateur et préférences d'affichage."""

    db: "Session"
    user: "User"
    lang: str
    locale: str
    timezone: str
    currency: str

    # -- Traduction --------------------------------------------------------
    def ui(self, key: str, **params: object) -> str:
        """Texte d'interface dans la langue courante."""
        return t(key, self.lang, **params)

    def ui_or(self, key: str, fallback: str, **params: object) -> str:
        """
        Texte traduit s'il existe, sinon `fallback` (textes venus des services).

        Utile pour les contenus produits par les services (scénarios de
        démonstration) tout en restant traduisibles dans les langues fournies.
        """
        catalogs = [CATALOGS.get(self.lang, {}), CATALOGS[DEFAULT_LANGUAGE]]
        if any(key in catalog for catalog in catalogs):
            return self.ui(key, **params)
        return fallback

    @property
    def language_label(self) -> str:
        return LANGUAGE_LABELS.get(self.lang, self.lang)


def build_context(db: "Session", user: "User") -> AppContext:
    """Construit le contexte d'affichage de l'utilisateur connecté."""
    lang = normalize_language(st.session_state.get("ui_lang") or user.locale)
    st.session_state["ui_lang"] = lang
    return AppContext(
        db=db,
        user=user,
        lang=lang,
        locale=user.locale or settings.default_locale,
        timezone=user.timezone or settings.app_timezone,
        currency=user.currency or settings.default_currency,
    )


__all__ = [
    "AppContext",
    "build_context",
    "current_user",
    "db_session",
    "language_selector",
]
