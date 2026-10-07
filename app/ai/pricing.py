"""Model pricing + a pure cost calculator.

Rates are USD **per 1,000,000 tokens**, split into input / output / cache-write
/ cache-read (some vendors price prompt-cache writes above and reads far below
the base input rate). Cost is computed once, at call time, and stored on the
usage row — so changing these rates never rewrites history.

Rates come from OpenRouter's own live catalog
(``app/integrations/llm/model_catalog.py``, ``GET /models``) — there is no
hardcoded per-model rate table here anymore. That fixes a real, verified bug:
the previous hand-maintained table had been overstating Anthropic costs by
1.5x-3x and had no entry at all for a model added after the table was last
updated (silently priced at $0). Reading live pricing means it can never go
stale and never needs a code change when a new model becomes available.

``AI_PRICING_JSON`` remains as an *admin-configurable* override layer (JSON:
``{"model": {"input":.., "output":.., "cache_write":.., "cache_read":..}}``,
values per 1M tokens) for the rare case the catalog is temporarily unreachable
or a specific rate needs a manual correction — checked first, before the live
catalog.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal

from app.core.config import get_settings

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


def _overrides() -> dict[str, ModelRate]:
    raw = get_settings().ai.pricing_json
    if not raw:
        return {}
    try:
        rates: dict[str, ModelRate] = {}
        for model, r in json.loads(raw).items():
            rates[model] = _rate(
                str(r["input"]),
                str(r["output"]),
                str(r.get("cache_write", r["input"])),
                str(r.get("cache_read", r["input"])),
            )
        return rates
    except Exception:  # bad override must never break AI calls
        logger.warning("Ignoring invalid AI_PRICING_JSON override", exc_info=True)
        return {}


def get_rate(model: str) -> ModelRate | None:
    """The priced rate for ``model``, or ``None`` if it's neither in the
    ``AI_PRICING_JSON`` override nor a currently-priced entry in OpenRouter's
    live catalog."""
    override = _overrides().get(model)
    if override is not None:
        return override

    # Imported here, not at module level: app.integrations.llm's package
    # __init__ pulls in base.py, which imports app.ai.usage, which imports
    # this module — a module-level import here would be a circular import
    # (confirmed live: it crashed the app on boot). By the time this function
    # actually runs, both modules are already fully initialized.
    from app.integrations.llm import model_catalog

    catalog_model = model_catalog.get_model(model)
    if catalog_model is None or catalog_model.input_per_million is None:
        return None
    # Not every catalog entry publishes cache pricing — default both to the
    # base input rate (a deliberately conservative "no cache discount
    # assumed") when it's missing, rather than treating a whole model as
    # unpriced just because one of four rates is absent.
    return ModelRate(
        input=catalog_model.input_per_million,
        output=catalog_model.output_per_million or catalog_model.input_per_million,
        cache_write=catalog_model.cache_write_per_million or catalog_model.input_per_million,
        cache_read=catalog_model.cache_read_per_million or catalog_model.input_per_million,
    )


def has_rate(model: str) -> bool:
    return get_rate(model) is not None


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
    rate = get_rate(model)
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
