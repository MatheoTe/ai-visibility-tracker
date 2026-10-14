"""Token pricing used for cost estimates (USD per million tokens).

These are list prices for estimation only - verify against each vendor's pricing
page before relying on them for billing. Unknown models are priced at zero and
flagged in logs rather than failing the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Price:
    input_per_mtok: Decimal
    output_per_mtok: Decimal


PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-4-5-20251001": Price(Decimal("1.00"), Decimal("5.00")),
    "claude-haiku-4-5": Price(Decimal("1.00"), Decimal("5.00")),
    "gpt-5-mini": Price(Decimal("0.25"), Decimal("2.00")),
    "gemini-2.5-flash": Price(Decimal("0.30"), Decimal("2.50")),
    "sonar": Price(Decimal("1.00"), Decimal("1.00")),
}

_MTOK = Decimal(1_000_000)
_QUANT = Decimal("0.000001")


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    price = PRICES.get(model)
    if price is None:
        return Decimal(0)
    cost = (
        Decimal(input_tokens) * price.input_per_mtok + Decimal(output_tokens) * price.output_per_mtok
    ) / _MTOK
    return cost.quantize(_QUANT)


def is_priced(model: str) -> bool:
    return model in PRICES
