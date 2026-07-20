"""AI cost-optimization heuristics.

Analyzes recorded AI usage (``ai_usage_events``, rolled up per feature+model) and
produces concrete, deterministic suggestions to reduce spend — chiefly routing
high-volume "data-gathering" steps from an expensive model to a cheaper one, and
enabling prompt caching where a feature re-sends large stable prompts.

Pure: it takes rolled-up rows and returns a report, so it is trivially testable
and needs no live provider. Savings are estimates recomputed from the same
pricing table used to bill the tokens, and are a ceiling, not a guarantee.
"""

from __future__ import annotations

from decimal import Decimal

from app.ai.features import AiFeature
from app.ai.pricing import MODEL_PRICING, UsageBreakdown, price
from app.schemas.ai_usage import CostOptimizationReport, CostSuggestion

# Features whose calls mostly gather/normalize data (not client-facing prose):
# safe to run on the cheapest capable model.
_DATA_GATHERING_FEATURES = {
    AiFeature.BRAND_EXTRACTION,
    AiFeature.CONSISTENCY_CHECK,
    AiFeature.MISSING_INFO,
    AiFeature.CLIENT_SUMMARY,
    AiFeature.WATCHDOG,
    AiFeature.HEALTH_SCORE,
}
# Expensive tier we route DOWN from.
_EXPENSIVE_MODELS = {"claude-opus-4-8"}
_CHEAP_MODEL = "claude-haiku-4-5-20251001"  # for data-gathering steps
_MID_MODEL = "claude-sonnet-5"  # for everything else on the expensive tier

# Don't bother suggesting for trivial amounts.
_MIN_SAVINGS = Decimal("0.01")
# Prompt-caching heuristic thresholds.
_CACHE_MIN_REQUESTS = 20
_CACHE_MIN_INPUT_TOKENS = 200_000


def build_report(rows: list[dict]) -> CostOptimizationReport:
    """Turn per-(feature, model) usage rows into a cost-optimization report."""
    analyzed_requests = sum(int(r.get("requests", 0)) for r in rows)
    analyzed_cost = sum(float(r.get("total_cost", 0.0)) for r in rows)

    suggestions: list[CostSuggestion] = []
    for r in rows:
        suggestions.extend(_row_suggestions(r))

    suggestions.sort(key=lambda s: s.estimated_savings, reverse=True)
    potential = round(sum(s.estimated_savings for s in suggestions), 6)
    return CostOptimizationReport(
        analyzed_requests=analyzed_requests,
        analyzed_cost=round(analyzed_cost, 6),
        potential_savings=potential,
        suggestions=suggestions,
    )


def _row_suggestions(r: dict) -> list[CostSuggestion]:
    feature = str(r.get("feature") or "")
    model = str(r.get("model") or "")
    requests = int(r.get("requests", 0))
    input_tokens = int(r.get("input_tokens", 0))
    output_tokens = int(r.get("output_tokens", 0))
    cache_read_tokens = int(r.get("cache_read_tokens", 0))
    cost = Decimal(str(r.get("total_cost", 0.0)))

    out: list[CostSuggestion] = []

    # ---- 1. cheaper-model routing off the expensive tier ----
    if model in _EXPENSIVE_MODELS and cost > 0:
        target = _CHEAP_MODEL if feature in _DATA_GATHERING_FEATURES else _MID_MODEL
        if target in MODEL_PRICING:
            projected = price(
                target,
                UsageBreakdown(input_tokens=input_tokens, output_tokens=output_tokens),
            ).total_cost
            savings = cost - projected
            if savings >= _MIN_SAVINGS:
                pct = int(savings / cost * 100) if cost else 0
                out.append(
                    CostSuggestion(
                        id=f"route-cheaper-model:{feature}:{model}",
                        title=f"Route '{feature}' to {target}",
                        detail=(
                            f"'{feature}' ran {requests} time(s) on {model}. "
                            f"{'This is a data-gathering step; ' if feature in _DATA_GATHERING_FEATURES else ''}"
                            f"routing it to {target} would cut cost with negligible quality risk."
                        ),
                        feature=feature,
                        current_model=model,
                        suggested_model=target,
                        estimated_savings=round(float(savings), 6),
                        savings_pct=pct,
                        confidence=80 if feature in _DATA_GATHERING_FEATURES else 60,
                    )
                )

    # ---- 2. prompt caching for high-volume, large, uncached prompts ----
    if (
        requests >= _CACHE_MIN_REQUESTS
        and input_tokens >= _CACHE_MIN_INPUT_TOKENS
        and cache_read_tokens == 0
        and model in MODEL_PRICING
    ):
        rate = MODEL_PRICING[model]
        # Cached reads are far cheaper than base input; assume ~60% of input is a
        # stable prefix that could be served from cache.
        cacheable = Decimal(input_tokens) * Decimal("0.6")
        saving = (rate.input - rate.cache_read) * cacheable / Decimal(1_000_000)
        if saving >= _MIN_SAVINGS:
            out.append(
                CostSuggestion(
                    id=f"enable-caching:{feature}:{model}",
                    title=f"Enable prompt caching for '{feature}'",
                    detail=(
                        f"'{feature}' sent {input_tokens:,} input tokens across "
                        f"{requests} calls on {model} with no cache reads. Caching the "
                        "stable prompt prefix (system + client rules) would cut input cost."
                    ),
                    feature=feature,
                    current_model=model,
                    suggested_model=model,
                    estimated_savings=round(float(saving), 6),
                    savings_pct=0,
                    confidence=55,
                )
            )
    return out
