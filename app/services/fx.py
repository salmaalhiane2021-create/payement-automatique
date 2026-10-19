"""
Conversion de devises — **désactivée par défaut**.

Choix explicite : le prototype ne fabrique aucun taux de change. Les montants
sont stockés en EUR (devise de base du jeu de démonstration) ; si l'utilisateur
a choisi une autre devise d'affichage, la conversion n'a lieu que si une source
fiable est configurée (``FX_RATES_JSON`` + ``FX_RATES_SOURCE``). Sinon les
interfaces affichent les montants en EUR en l'indiquant clairement.

Pour brancher une source réelle, implémentez un `FxConverter` (API d'un
fournisseur, table interne validée…) et retournez-le depuis `get_converter`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from app.config import settings


class FxConverter(Protocol):
    """Contrat minimal d'un fournisseur de taux de change."""

    name: str

    def convert(self, amount: float, base: str, quote: str) -> float | None:
        """Convertit `amount` de `base` vers `quote`, ou None si indisponible."""
        ...


@dataclass
class StaticRatesConverter:
    """
    Taux fixes fournis par configuration (tests, démonstration encadrée).

    `rates` exprime le nombre d'unités de la devise pour 1 EUR.
    """

    rates: dict[str, float] = field(default_factory=dict)
    source: str = "configuration statique"

    @property
    def name(self) -> str:
        return self.source

    def convert(self, amount: float, base: str, quote: str) -> float | None:
        base_code = (base or "").upper()
        quote_code = (quote or "").upper()
        if base_code == quote_code:
            return round(float(amount), 2)
        if base_code != "EUR":
            # Aucun taux croisé n'est inventé : seule la base EUR est connue.
            return None
        rate = self.rates.get(quote_code)
        if not rate:
            return None
        return round(float(amount) * float(rate), 2)


def get_converter() -> FxConverter | None:
    """
    Fournisseur actif, ou None si aucune source fiable n'est configurée.

    `FX_RATES_JSON` attend un objet JSON : ``{"USD": 1.08, "GBP": 0.85}``.
    """
    raw = (settings.fx_rates_json or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    rates: dict[str, float] = {}
    for code, value in parsed.items():
        try:
            rate = float(value)
        except (TypeError, ValueError):
            continue
        if rate > 0 and len(str(code)) == 3:
            rates[str(code).upper()] = rate
    if not rates:
        return None
    return StaticRatesConverter(
        rates=rates,
        source=settings.fx_rates_source or "FX_RATES_JSON",
    )


def convert(amount: float, base: str, quote: str) -> float | None:
    """Convertit si possible ; None signifie « conversion indisponible »."""
    base_code = (base or "EUR").upper()
    quote_code = (quote or "EUR").upper()
    if base_code == quote_code:
        return round(float(amount), 2)
    converter = get_converter()
    if converter is None:
        return None
    return converter.convert(float(amount), base_code, quote_code)


__all__ = ["FxConverter", "StaticRatesConverter", "convert", "get_converter"]
