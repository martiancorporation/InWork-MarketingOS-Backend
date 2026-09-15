"""Model pricing + a pure cost calculator.

Rates are USD **per 1,000,000 tokens**, split into input / output / cache-write
/ cache-read (some vendors price prompt-cache writes above and reads far below
the base input rate). Cost is computed once, at call time, and stored on the
usage row — so changing these rates never rewrites history.

Keyed by the exact OpenRouter model id string (e.g. ``"anthropic/claude-opus-5"``)
passed to the API — see ``app/integrations/llm/``.

Rates below were verified directly against OpenRouter's live catalog
(``GET https://openrouter.ai/api/v1/models``) in September 2026 — replacing an
earlier placeholder table that had been overstating Anthropic costs by
1.5x-3x. Re-run ``scripts/refresh_model_pricing.py`` periodically (or after
adding a model to ``app/ai/model_router.py``'s ``KNOWN_MODELS``) to catch
drift; OpenRouter prices can change without notice. Override at runtime with
the ``AI_PRICING_JSON`` env var (JSON:
``{"model": {"input":.., "output":.., "cache_write":.., "cache_read":..}}``,
values per 1M tokens).
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


# Verified against OpenRouter's live /models catalog (Sept 2026).
_DEFAULT_PRICING: dict[str, ModelRate] = {
    # --- Anthropic (via OpenRouter) ---
    "anthropic/claude-opus-5": _rate("5", "25", "6.25", "0.50"),
    # "claude-opus-4-8" (the configured OPENROUTER_MODEL default before this
    # fix) no longer appears in OpenRouter's public catalog but still serves
    # real requests (confirmed live) — priced at parity with opus-5 as a
    # best-effort estimate so historical/legacy usage rows stay priced rather
    # than silently zeroing out.
    "anthropic/claude-opus-4-8": _rate("5", "25", "6.25", "0.50"),
    "anthropic/claude-sonnet-5": _rate("2", "10", "2.5", "0.20"),
    "anthropic/claude-haiku-4.5": _rate("1", "5", "1.25", "0.10"),
    "anthropic/claude-fable-5": _rate("10", "50", "12.5", "1.00"),
    "anthropic/claude-fable-5.1": _rate("10", "50", "12.5", "1.00"),
    # --- new low-cost models (Ayon Das's Sept 2026 recommendations) ---
    "openai/gpt-5.6-luna": _rate("0.20", "1.20", "0.25", "0.02"),
    "z-ai/glm-5.3-flash": _rate("0.15", "0.50", "0.15", "0.03"),
    "deepseek/deepseek-v4.1-flash": _rate("0.15", "0.60", "0.15", "0.003"),
    "minimax/minimax-m3": _rate("0.30", "1.20", "0.30", "0.06"),
    "qwen/qwen3.7-flash": _rate("0.03", "0.13", "0.038", "0.006"),
}


def _load_pricing() -> dict[str, ModelRate]:
    rates = dict(_DEFAULT_PRICING)
    raw = get_settings().ai.pricing_json
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
