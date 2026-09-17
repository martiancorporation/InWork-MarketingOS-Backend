"""Per-feature LLM model routing — dynamic, DB-backed, research-based.

Every AI surface maps to a small, stable **task category** (classification,
extraction, summarization, structured generation, analysis, conversational,
or reasoning-complex) rather than being hand-tuned one feature at a time.
Each category resolves to a model through ``ai_model_routes`` (an
admin-editable table, see ``app.models.ai_model_route.AiModelRoute`` +
``app.api.v1.routers.ai_model_routes``) so the actual model choice can be
retuned at any time — by an admin, from real usage/quality data — with no
code change or redeploy.

Why not have the AI pick its own model per call? That would add its own
latency and cost to every single request, working against the point of this
module. What "dynamic" means here is that the category → model mapping is
research-backed and live-updatable, not baked into a code deploy — a second,
literal AI-routing-decision layer is a separate, addable feature on top of
this table if a specific high-value case ever needs it.

Graceful degradation: the DB lookup goes through a short-lived, auto-expiring
cache (``_CACHE_TTL_SECONDS``) that is invalidated immediately on any write
(see ``AiModelRouteService``). If the table is empty, unreachable, or not yet
migrated, every category quietly falls back to ``_BUILTIN_DEFAULTS`` — the
same house stance as every other AI feature (never hard-require external
state). ``model_for`` only ever *downgrades* from the configured default
model (``AISettings.model``); an unmapped feature keeps today's behavior.
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
    # model_for() either, so every command turn silently ran on the raw
    # ceiling default (AISettings.model) instead of this category's tuned
    # model. See app/ai/command_agent.py.
    AiFeature.COMMAND_AGENT: AiTaskCategory.CONVERSATIONAL,
    # Small, cheap "does this chat message want a content plan, and is the
    # date range clear" check that runs ahead of every Ask AI turn — see
    # app/ai/plan_chat_intent.py. Classification-shaped, not conversational.
    AiFeature.PLAN_CHAT_INTENT: AiTaskCategory.CLASSIFICATION,
}

# Research-backed starting point (Sept 2026 OpenRouter catalog + pricing —
# see app/ai/pricing.py for the verified per-token rates). A first pass for
# the team to tune from real output-quality testing, not a final verdict;
# change it any time via the /admin/ai-model-routes API — no deploy needed.
#
# CONVERSATIONAL was re-picked 2026-09-17 after a client cost/speed complaint
# traced to AiFeature.COMMAND_AGENT (the unified chat's tool-calling loop —
# see its comment in FEATURE_CATEGORY above) silently bypassing this table
# entirely and running every turn on the flagship ceiling default. Once wired
# in, "openai/gpt-5.6-luna" ($0.20/$1.20 per 1M) was swapped for
# "deepseek/deepseek-v4.1-flash" ($0.15/$0.60 per 1M): cheaper, "flash"-tier
# (fast), and already the trusted choice here for EXTRACTION and
# STRUCTURED_GENERATION — the closest proxy for tool-calling reliability
# (both are "produce well-formed structured output from instructions"), which
# is the core skill this category's heaviest caller (the command agent) needs
# alongside plain conversational replies.
_BUILTIN_DEFAULTS: dict[str, str] = {
    AiTaskCategory.CLASSIFICATION: "qwen/qwen3.7-flash",
    AiTaskCategory.EXTRACTION: "deepseek/deepseek-v4.1-flash",
    AiTaskCategory.SUMMARIZATION: "z-ai/glm-5.3-flash",
    AiTaskCategory.STRUCTURED_GENERATION: "deepseek/deepseek-v4.1-flash",
    AiTaskCategory.ANALYSIS: "minimax/minimax-m3",
    AiTaskCategory.CONVERSATIONAL: "deepseek/deepseek-v4.1-flash",
    AiTaskCategory.REASONING_COMPLEX: "anthropic/claude-sonnet-5",
}

# The fixed catalog offered in the admin routing UI's model picker — a small,
# curated static list (not a live OpenRouter catalog fetch), so the dropdown
# never depends on network access and can't be pointed at a typo'd model id.
# (label, model_id, input_per_1m, output_per_1m) — prices are informational,
# the real numbers billed still come from app/ai/pricing.py.
KNOWN_MODELS: list[dict[str, str | float]] = [
    {"model_id": "qwen/qwen3.7-flash", "label": "Qwen 3.7 Flash", "input": 0.03, "output": 0.13},
    {
        "model_id": "deepseek/deepseek-v4.1-flash",
        "label": "DeepSeek V4.1 Flash",
        "input": 0.15,
        "output": 0.60,
    },
    {
        "model_id": "z-ai/glm-5.3-flash",
        "label": "GLM 5.3 Flash",
        "input": 0.15,
        "output": 0.50,
    },
    {
        "model_id": "minimax/minimax-m3",
        "label": "Minimax M3",
        "input": 0.30,
        "output": 1.20,
    },
    {
        "model_id": "openai/gpt-5.6-luna",
        "label": "GPT 5.6 Luna",
        "input": 0.20,
        "output": 1.20,
    },
    {
        "model_id": "anthropic/claude-haiku-4.5",
        "label": "Claude Haiku 4.5",
        "input": 1.0,
        "output": 5.0,
    },
    {
        "model_id": "anthropic/claude-sonnet-5",
        "label": "Claude Sonnet 5",
        "input": 2.0,
        "output": 10.0,
    },
    {
        "model_id": "anthropic/claude-opus-5",
        "label": "Claude Opus 5",
        "input": 5.0,
        "output": 25.0,
    },
]
KNOWN_MODEL_IDS: frozenset[str] = frozenset(str(m["model_id"]) for m in KNOWN_MODELS)

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

    Never raises: an empty dict here just means every category falls back to
    its built-in default below — the same "DB is an optional override, not a
    hard dependency" stance ``app/ai/usage.py::record_usage`` already takes
    (and, like that function, this uses its own short-lived session via
    ``get_session_factory()`` rather than threading a ``db: Session`` through
    every AI agent — most of them don't have one).
    """
    try:
        from sqlalchemy import select

        from app.models.ai_model_route import AiModelRoute

        with get_session_factory()() as db:
            rows = db.scalars(select(AiModelRoute).where(AiModelRoute.is_active.is_(True))).all()
            return {row.task_category: row.model_id for row in rows}
    except Exception:
        logger.warning("Failed to load ai_model_routes — using built-in defaults", exc_info=True)
        return {}


def _active_routes() -> dict[str, str]:
    global _cache, _cache_loaded_at
    now = time.monotonic()
    if _cache is None or (now - _cache_loaded_at) > _CACHE_TTL_SECONDS:
        _cache = _load_active_routes()
        _cache_loaded_at = now
    return _cache


def model_for(feature: str | None) -> str | None:
    """Model id to pass as a per-call override, or ``None`` to keep today's default."""
    if not feature:
        return None
    category = FEATURE_CATEGORY.get(feature)
    if category is None:
        return None
    return _active_routes().get(category) or _BUILTIN_DEFAULTS.get(category)


def category_for(feature: str | None) -> str | None:
    """The task category a feature belongs to, or ``None`` if unmapped."""
    if not feature:
        return None
    return FEATURE_CATEGORY.get(feature)


def builtin_default(category: str) -> str | None:
    """The built-in fallback model for a category (used when seeding the
    admin list and when the DB has no active row yet)."""
    return _BUILTIN_DEFAULTS.get(category)
