"""
Cohérence de l'interface Streamlit avec le catalogue i18n.

Ces tests garantissent, sans lancer de navigateur :

* que les catalogues français et anglais exposent **exactement** les mêmes clés ;
* que tous les modules de `streamlit_ui/` s'importent sans dépendance manquante ;
* que chaque clé de traduction littérale utilisée dans l'interface existe
  (les familles construites dynamiquement, ex. `f"nav.{page}"`, sont vérifiées
  explicitement).

Exécution :
    .venv\\Scripts\\python -m pytest tests/test_ui_keys.py -q
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from app.i18n import CATALOGS

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "streamlit_ui"

#: Appels de traduction suivis dans l'interface : `ctx.ui(...)`, `ctx.ui_or(...)`, `t(...)`.
KEY_CALL = re.compile(r"""(?<![a-zA-Z_])(?:ui|ui_or|t)\(\s*["']([a-zA-Z0-9_.]+)["']""")

#: Clés construites par f-string dans l'interface (vérifiées explicitement).
DYNAMIC_KEYS = [
    "nav.dashboard",
    "nav.transactions",
    "nav.payment",
    "nav.security",
    "nav.model",
    "nav.language",
    "nav.region",
    "nav.logout",
    "nav.unread_alerts",
    "nav.no_unread",
    "nav.otp_ttl",
    "nav.thresholds",
    "nav.navigation",
    "security.kind_securite",
    "security.kind_epargne",
    "security.kind_budget",
    "payment.scenario.habituel.title",
    "payment.scenario.habituel.description",
    "payment.scenario.montant.title",
    "payment.scenario.montant.description",
    "payment.scenario.appareil.title",
    "payment.scenario.appareil.description",
    "payment.scenario.voyage.title",
    "payment.scenario.voyage.description",
    "payment.scenario.nuit.title",
    "payment.scenario.nuit.description",
    "factor.zone_inhabituelle",
    "factor.montant_inhabituel",
    "factor.appareil_inconnu",
    "decision.accept_short",
    "decision.verify_short",
    "decision.block_short",
    "decision.accept",
    "decision.verify",
    "decision.block",
    "severity.critique",
    "severity.eleve",
    "severity.moyen",
    "severity.faible",
    "status.completed",
    "status.blocked",
    "status.pending",
    "status.cancelled",
    "kind.fraude",
    "kind.anomalie",
    "kind.securite",
    "kind.info",
]

UI_MODULES = [
    "streamlit_ui.context",
    "streamlit_ui.components",
    "streamlit_ui.formatting",
    "streamlit_ui.styles",
    "streamlit_ui.demo",
    "streamlit_ui.login",
    "streamlit_ui.shell",
    "streamlit_ui.main",
    "streamlit_ui.pages.dashboard",
    "streamlit_ui.pages.transactions",
    "streamlit_ui.pages.payment",
    "streamlit_ui.pages.security",
    "streamlit_ui.pages.model",
]


def test_catalogs_share_the_same_keys() -> None:
    """Le français et l'anglais doivent rester rigoureusement alignés."""
    assert set(CATALOGS["fr"]) == set(CATALOGS["en"])


@pytest.mark.parametrize("module_name", UI_MODULES)
def test_ui_module_imports(module_name: str) -> None:
    """Chaque module d'interface s'importe (aucune règle métier dupliquée)."""
    assert importlib.import_module(module_name) is not None


def test_literal_translation_keys_exist() -> None:
    """Toute clé littérale utilisée dans l'interface existe en fr et en anglais."""
    used: set[str] = set()
    for path in sorted(UI_DIR.rglob("*.py")):
        used.update(KEY_CALL.findall(path.read_text(encoding="utf-8")))

    assert used, "aucune clé détectée : le motif de recherche est probablement obsolète"
    missing = sorted(key for key in used if key not in CATALOGS["fr"] or key not in CATALOGS["en"])
    assert missing == [], f"clés i18n manquantes dans l'interface : {missing}"


def test_dynamic_translation_keys_exist() -> None:
    """Les clés construites dynamiquement existent aussi."""
    missing = sorted(
        key for key in DYNAMIC_KEYS if key not in CATALOGS["fr"] or key not in CATALOGS["en"]
    )
    assert missing == [], f"clés i18n dynamiques manquantes : {missing}"
