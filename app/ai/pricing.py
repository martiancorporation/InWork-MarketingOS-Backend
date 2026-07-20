"""Model pricing + a pure cost calculator.

Rates are USD **per 1,000,000 tokens**, split into input / output / cache-write
/ cache-read (Anthropic prices prompt-cache writes above and reads far below the
base input rate). Cost is computed once, at call time, and stored on the usage
row — so changing these rates never rewrites history.

⚠️ PLACEHOLDER RATES — verify each number against your official Anthropic
pricing / negotiated contract before trusting the dollar figures. Override at
runtime with the ``AI_PRICING_JSON`` env var (JSON: ``{"model": {"input":..,
"output":.., "cache_write":.., "cache_read":..}}``, values per 1M tokens).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)

_MILLION = Decimal(1_000_000)
_CENT = Decimal("0.000001")  # store to 6 dp


@dataclass(frozen=True)
class ModelRate:
    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_read: Decimal


def _rate(inp: str, out: str, cw: str, cr: str) -> ModelRate:
    return ModelRate(Decimal(inp), Decimal(out), Decimal(cw), Decimal(cr))


# PLACEHOLDER defaults — confirm against real pricing.
_DEFAULT_PRICING: dict[str, ModelRate] = {
    "claude-opus-4-8": _rate("15", "75", "18.75", "1.50"),
    "claude-sonnet-5": _rate("3", "15", "3.75", "0.30"),
    "claude-haiku-4-5-20251001": _rate("1", "5", "1.25", "0.10"),
    "claude-fable-5": _rate("3", "15", "3.75", "0.30"),
}


def _load_pricing() -> dict[str, ModelRate]:
    rates = dict(_DEFAULT_PRICING)
    raw = os.getenv("AI_PRICING_JSON")
    if raw:
        try:
            for model, r in json.loads(raw).items():
                rates[model] = _rate(
                    str(r["input"]),
                    str(r["output"]),
                    str(r.get("cache_write", r["input"])),
                    str(r.get("cache_read", r["input"])),
                )
        except Exception:  # bad override must never break AI calls
            logger.warning("Ignoring invalid AI_PRICING_JSON override", exc_info=True)
    return rates


MODEL_PRICING: dict[str, ModelRate] = _load_pricing()


@dataclass(frozen=True)
class UsageBreakdown:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_write_tokens
            + self.cache_read_tokens
        )


@dataclass(frozen=True)
class CostBreakdown:
    input_cost: Decimal
    output_cost: Decimal
    cache_cost: Decimal
    total_cost: Decimal
    priced: bool  # False when the model has no rate entry


def price(model: str, usage: UsageBreakdown) -> CostBreakdown:
    """Compute the USD cost of ``usage`` for ``model``. Unknown model → zero cost
    with ``priced=False`` (tokens are still recorded upstream)."""
    rate = MODEL_PRICING.get(model)
    if rate is None:
        logger.warning("No pricing for model %r — recording tokens with zero cost", model)
        z = Decimal(0)
        return CostBreakdown(z, z, z, z, priced=False)

    ic = rate.input * usage.input_tokens / _MILLION
    oc = rate.output * usage.output_tokens / _MILLION
    cc = (
        rate.cache_write * usage.cache_write_tokens + rate.cache_read * usage.cache_read_tokens
    ) / _MILLION
    total = ic + oc + cc
    return CostBreakdown(
        ic.quantize(_CENT), oc.quantize(_CENT), cc.quantize(_CENT), total.quantize(_CENT), True
    )
