"""Identifiants du jeu de démonstration (aucune logique métier)."""

from __future__ import annotations

DEMO_EMAIL = "demo@nexpay.fr"
DEMO_PASSWORD = "Demo1234!"
DEMO_PIN = "4321"

DEMO_ACCOUNTS = [
    ("demo@nexpay.fr", "Camille Bernard", "compte principal — fraudes simulées"),
    ("yassine@nexpay.fr", "Yassine El Amrani", "profil du soir"),
    ("lea@nexpay.fr", "Léa Moreau", "profil du jour"),
]

__all__ = ["DEMO_ACCOUNTS", "DEMO_EMAIL", "DEMO_PASSWORD", "DEMO_PIN"]
