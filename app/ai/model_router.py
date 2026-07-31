"""Per-feature Anthropic model routing.

Not every AI surface needs the top-tier model: features that just extract,
classify, or gather structured facts (no client-facing prose, low creative
bar) route to a cheaper model. Client-facing conversational surfaces (Ask AI,
the global assistant) are left on the configured default (today: Opus) by
simply not overriding — ``model_for`` only ever *downgrades*, never upgrades,
so an unmapped feature keeps exactly today's behavior.

The tiers themselves (``cheap_model``/``mid_model``) are read from
``AISettings`` (``app/core/config/ai.py``), not hard-coded here — the same
tiers ``app/ai/cost_optimization.py``'s advisory report already references.
``CHEAP_TIER_FEATURES`` is the single source of truth for "safe to run cheap";
``cost_optimization.py`` imports it too, so the advisory report and live
routing can never drift apart.
"""

from __future__ import annotations

from app.ai.features import AiFeature
from app.core.config import get_settings

# Data-gathering / extraction / classification steps — negligible quality risk
# on the cheap tier. "intelligence.build" is the shared usage label
# SummaryAgent + DirectivesAgent already use (app/services/intelligence/orchestrator.py).
CHEAP_TIER_FEATURES = frozenset(
    {
        AiFeature.BRAND_EXTRACTION,
        AiFeature.CONSISTENCY_CHECK,
        AiFeature.MISSING_INFO,
        AiFeature.CLIENT_SUMMARY,
        AiFeature.WATCHDOG,
        AiFeature.HEALTH_SCORE,
        "intelligence.build",
    }
)

# Client-facing prose that benefits from a stronger model than the cheap tier,
# but doesn't need the flagship reserved for direct conversational Ask AI.
MID_TIER_FEATURES = frozenset(
    {
        AiFeature.EXECUTIVE_BRIEF,
        AiFeature.RECOMMENDATION,
        AiFeature.OPPORTUNITY,
        AiFeature.CONTENT_REVIEW,
    }
)


def model_for(feature: str | None) -> str | None:
    """Model id to pass as a per-call override, or ``None`` to keep today's default."""
    if not feature:
        return None
    ai = get_settings().ai
    if feature in CHEAP_TIER_FEATURES:
        return ai.cheap_model
    if feature in MID_TIER_FEATURES:
        return ai.mid_model
    return None
