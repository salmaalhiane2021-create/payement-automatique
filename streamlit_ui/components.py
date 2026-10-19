"""Briques visuelles réutilisables (aucune règle métier, aucune traduction en dur)."""

from __future__ import annotations

import html

import streamlit as st

from streamlit_ui.context import AppContext


def badge(label: str, css_class: str) -> str:
    """Pastille colorée (décision, gravité, recommandation)."""
    return f'<span class="nx-pill {css_class}">{html.escape(label)}</span>'


def kpis(items: list[dict]) -> None:
    """Cartes d'indicateurs responsives (grille qui s'adapte à la largeur)."""
    cards = []
    for item in items:
        delta = item.get("delta")
        if delta:
            tone = item.get("tone", "flat")
            arrow = {"up": "↑ ", "down": "↓ ", "flat": ""}.get(tone, "")
            delta_html = f'<div class="nx-kpi-delta nx-{tone}">{arrow}{html.escape(str(delta))}</div>'
        else:
            delta_html = '<div class="nx-kpi-delta">&nbsp;</div>'
        cards.append(
            f'<div class="nx-kpi"><div class="nx-kpi-label">{html.escape(item["label"])}</div>'
            f'<div class="nx-kpi-value">{html.escape(str(item["value"]))}</div>{delta_html}</div>'
        )
    st.markdown(f'<div class="nx-kpi-grid">{"".join(cards)}</div>', unsafe_allow_html=True)


def hero(kicker: str, title: str, subtitle: str) -> None:
    """En-tête de page (surtitre, titre, sous-titre déjà échappés si besoin)."""
    st.markdown(
        f'<div class="nx-hero"><div class="nx-kicker">{html.escape(kicker)}</div>'
        f"<h1>{title}</h1><p>{subtitle}</p></div>",
        unsafe_allow_html=True,
    )


def brand(ctx: AppContext) -> str:
    """Bloc de marque affiché dans la barre latérale et sur la connexion."""
    return (
        '<div class="nx-brand"><span class="nx-logo">N</span>'
        f'<div class="nx-brand-title">NexPay <span>AI</span>'
        f'<small>{html.escape(ctx.ui("app.brand_subtitle"))}</small></div></div>'
    )


__all__ = ["badge", "brand", "hero", "kpis"]
