"""
Test de fumée de l'interface Streamlit via `AppTest` (sans navigateur).

Parcourt le parcours réel : page de connexion, authentification par le service
partagé, navigation sur les cinq pages, bascule français -> anglais et
lancement d'un scénario de paiement (affichage du code à usage unique).

Ce test est **désactivé par défaut** : il démarre l'application, amorce la base
(configurée par `DATABASE_URL`, sinon `data/nexpay.db`) et écrit quelques
opérations de démonstration (connexion, défi de paiement).

Exécution explicite :

    .venv\\Scripts\\python -m pytest tests/test_ui_smoke.py -q
    # ou, sous PowerShell :
    $env:NEXPAY_UI_SMOKE='1'; .venv\\Scripts\\python -m pytest tests/test_ui_smoke.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    os.environ.get("NEXPAY_UI_SMOKE") != "1",
    reason="test d'interface désactivé : définir NEXPAY_UI_SMOKE=1 pour l'exécuter",
)

DEMO_EMAIL = "demo@nexpay.fr"
DEMO_PASSWORD = "Demo1234!"


def _page_text(at) -> str:
    parts = [element.value for element in at.markdown]
    parts += [element.value for element in at.text]
    parts += [element.value for element in at.caption]
    parts += [element.label for element in at.button]
    return "\n".join(str(part) for part in parts)


def _assert_no_exception(at, label: str) -> None:
    assert not at.exception, f"exception pendant « {label} » : {[e.value for e in at.exception]}"


def test_login_navigation_language_and_scenario() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=180)
    app.run()

    # 1. Page de connexion -------------------------------------------------
    _assert_no_exception(app, "connexion")
    assert "NexPay" in _page_text(app)
    assert len(app.text_input) >= 2

    # 2. Authentification (service partagé) --------------------------------
    app.text_input[0].input(DEMO_EMAIL)
    app.text_input[1].input(DEMO_PASSWORD)
    app.button[0].click().run()
    _assert_no_exception(app, "après connexion")
    assert len(app.radio) == 1, "la navigation doit être affichée une fois connecté"
    assert app.radio[0].value == "dashboard"
    assert "Camille" in _page_text(app)

    # 3. Les cinq pages s'affichent sans exception -------------------------
    for page_key in ["transactions", "payment", "security", "model"]:
        app.radio[0].set_value(page_key).run()
        _assert_no_exception(app, f"page {page_key}")

    # 4. Bascule de langue : l'interface anglaise doit rester sur la page ---
    app.sidebar.selectbox[0].set_value("en").run()
    _assert_no_exception(app, "bascule en anglais")
    assert app.radio[0].value == "model", "la page active est conservée au changement de langue"
    assert "Model storage" in _page_text(app)

    # 5. Scénario de paiement : le code à usage unique doit s'afficher -----
    app.radio[0].set_value("payment").run()
    _assert_no_exception(app, "page paiement")
    scenarios = [
        button
        for button in app.button
        if getattr(button, "key", None) and str(button.key).startswith("scenario_")
    ]
    assert len(scenarios) >= 5, "les scénarios de démonstration doivent être proposés"
    scenarios[0].click().run()
    _assert_no_exception(app, "analyse du scénario habituel")
    code_fields = [field for field in app.text_input if "code" in field.label.lower()]
    assert code_fields, "le panneau du code à usage unique doit être affiché"
