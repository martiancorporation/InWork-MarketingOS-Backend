"""Per-feature LLM model routing — dynamic, DB-backed, provider-agnostic.

Every AI surface maps to a small, stable **task category** (classification,
extraction, summarization, structured generation, analysis, conversational,
or reasoning-complex) rather than being hand-tuned one feature at a time.
Each category resolves to a model through ``ai_model_routes`` (an
admin-editable table, see ``app.models.ai_model_route.AiModelRoute`` +
``app.api.v1.routers.ai_model_routes``) so the actual model choice — any
model currently in OpenRouter's live catalog, from any vendor it proxies, not
a fixed list this codebase maintains — can be retuned at any time by an
admin, from real usage/quality data, with no code change or redeploy.

Why not have the AI pick its own model per call? That would add its own
latency and cost to every single request, working against the point of this
module. What "dynamic" means here is that the category → model mapping is
admin-tunable and live-updatable, not baked into a code deploy — a second,
literal AI-routing-decision layer is a separate, addable feature on top of
this table if a specific high-value case ever needs it.

Graceful degradation: the DB lookup goes through a short-lived, auto-expiring
cache (``_CACHE_TTL_SECONDS``) that is invalidated immediately on any write
(see ``AiModelRouteService``). If a category has no active DB row — table
empty, unreachable, not yet migrated, or simply never configured — ``model_for``
returns ``None``: the same "unconfigured" signal ``LLMClient`` already treats
as "AI provider not configured" (see ``app/integrations/llm/openrouter.py``),
so every feature's existing deterministic fallback covers this automatically.
There is deliberately no hardcoded fallback model anywhere in this module —
the one bootstrap value a brand-new/empty install needs is computed from the
live catalog (see ``AiModelRouteService.list_routes``'s self-heal), never a
literal model id in code.
"""

from __future__ import annotations

import logging
import time

from app.ai.features import AiFeature
from app.db.session import get_session_factory

logger = logging.getLogger("app.ai.model_router")


class AiTaskCategory:
    """The fixed, small set of "kinds of AI work" every feature maps to.

    Not to be confused with ``app.models.enums.TaskCategory`` (content/dev/
    seo/... — the calendar/plan-task's own subject-matter category); this is
    a completely different axis: what KIND of model capability the call needs.
    """

    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    SUMMARIZATION = "summarization"
    STRUCTURED_GENERATION = "structured_generation"
    ANALYSIS = "analysis"
    CONVERSATIONAL = "conversational"
    REASONING_COMPLEX = "reasoning_complex"  # reserved ceiling — unused today


ALL_CATEGORIES: tuple[str, ...] = (
    AiTaskCategory.CLASSIFICATION,
    AiTaskCategory.EXTRACTION,
    AiTaskCategory.SUMMARIZATION,
    AiTaskCategory.STRUCTURED_GENERATION,
    AiTaskCategory.ANALYSIS,
    AiTaskCategory.CONVERSATIONAL,
    AiTaskCategory.REASONING_COMPLEX,
)

# Which category each AI feature's call belongs to. Only features that
# actually consult `model_for()` need an entry — QA_REVIEW, for instance,
# runs on a separate provider (OpenAI, off by default; see app/ai/qa.py) and
# never reaches this module, so it's deliberately not listed here.
FEATURE_CATEGORY: dict[str, str] = {
    AiFeature.BRAND_EXTRACTION: AiTaskCategory.EXTRACTION,
    AiFeature.CONSISTENCY_CHECK: AiTaskCategory.CLASSIFICATION,
    AiFeature.MISSING_INFO: AiTaskCategory.CLASSIFICATION,
    AiFeature.CLIENT_SUMMARY: AiTaskCategory.SUMMARIZATION,
    AiFeature.CLIENT_DIRECTIVES: AiTaskCategory.SUMMARIZATION,
    AiFeature.WATCHDOG: AiTaskCategory.ANALYSIS,
    AiFeature.HEALTH_SCORE: AiTaskCategory.ANALYSIS,
    AiFeature.EXECUTIVE_BRIEF: AiTaskCategory.ANALYSIS,
    AiFeature.RECOMMENDATION: AiTaskCategory.ANALYSIS,
    AiFeature.OPPORTUNITY: AiTaskCategory.ANALYSIS,
    AiFeature.CONTENT_REVIEW: AiTaskCategory.CLASSIFICATION,
    AiFeature.REPORT_NARRATIVE: AiTaskCategory.SUMMARIZATION,
    AiFeature.PLAN_GENERATION: AiTaskCategory.STRUCTURED_GENERATION,
    AiFeature.PROJECT_AI: AiTaskCategory.CONVERSATIONAL,
    AiFeature.ASSISTANT: AiTaskCategory.CONVERSATIONAL,
    AiFeature.DAY_CHAT: AiTaskCategory.CONVERSATIONAL,
    # The natural-language command layer's tool-calling loop (the AI chat's
    # actual engine since Ask/Command were unified into one flow) — same
    # repeat of the PROJECT_AI/ASSISTANT bug below: it used to never consult
    # model_for() at all, so every command turn silently ran on whatever
    # model was hardcoded as the global ceiling default instead of this
    # category's tuned model. See app/ai/command_agent.py.
    AiFeature.COMMAND_AGENT: AiTaskCategory.CONVERSATIONAL,
    # Small, cheap "does this chat message want a content plan, and is the
    # date range clear" check that runs ahead of every Ask AI turn — see
    # app/ai/plan_chat_intent.py. Classification-shaped, not conversational.
    AiFeature.PLAN_CHAT_INTENT: AiTaskCategory.CLASSIFICATION,
}

# Backstop so a stale/failed cache eventually retries the DB rather than
# freezing on whatever it last saw — same role as DashboardService's
# _SNAPSHOT_MAX_AGE_HOURS, just much shorter since this is a cheap read.
_CACHE_TTL_SECONDS = 30.0

_cache: dict[str, str] | None = None
_cache_loaded_at: float = 0.0


def invalidate_cache() -> None:
    """Drop the in-process route cache. Call after any write to
    ``ai_model_routes`` so the change is picked up by the next AI call in
    this worker immediately, rather than waiting out the TTL."""
    global _cache
    _cache = None


def _load_active_routes() -> dict[str, str]:
    """Active ``task_category -> model_id`` overrides from the DB.

    Never raises: a category missing here just means ``model_for`` returns
    ``None`` for it (treated as "AI not configured" by the caller) — the same
    "DB is an optional override, not a hard dependency" stance
    ``app/ai/usage.py::record_usage`` already takes (and, like that function,
    this uses its own short-lived session via ``get_session_factory()``
    rather than threading a ``db: Session`` through every AI agent — most of
    them don't have one).
    """
    try:
        from sqlalchemy import select

        from app.models.ai_model_route import AiModelRoute

        with get_session_factory()() as db:
            rows = db.scalars(
                select(AiModelRoute).where(
                    AiModelRoute.is_active.is_(True), AiModelRoute.model_id.is_not(None)
                )
            ).all()
            return {row.task_category: row.model_id for row in rows}
    except Exception:
        logger.warning(
            "Failed to load ai_model_routes — treating every category as unconfigured",
            exc_info=True,
        )
        return {}


def _active_routes() -> dict[str, str]:
    global _cache, _cache_loaded_at
    now = time.monotonic()
    if _cache is None or (now - _cache_loaded_at) > _CACHE_TTL_SECONDS:
        _cache = _load_active_routes()
        _cache_loaded_at = now
    return _cache


def model_for(feature: str | None) -> str | None:
    """Model id to pass as a per-call override, or ``None`` when this
    feature's category has no active, admin-configured route yet — the
    caller's ``LLMClient`` treats that identically to "AI not configured"
    (see ``app/integrations/llm/openrouter.py``)."""
    if not feature:
        return None
    category = FEATURE_CATEGORY.get(feature)
    if category is None:
        return None
    return _active_routes().get(category)


def category_for(feature: str | None) -> str | None:
    """The task category a feature belongs to, or ``None`` if unmapped."""
    if not feature:
        return None
    return FEATURE_CATEGORY.get(feature)


def bootstrap_model_id() -> str | None:
    """A safe, non-hardcoded starting point for a task category nobody has
    configured yet — the live catalog's cheapest priced model, or ``None`` if
    the catalog can't be reached at all (leaves the category unconfigured
    rather than guessing a literal id). See
    ``AiModelRouteService.list_routes``'s self-heal."""
    from app.integrations.llm import model_catalog

    model = model_catalog.cheapest_model()
    return model.id if model else None
