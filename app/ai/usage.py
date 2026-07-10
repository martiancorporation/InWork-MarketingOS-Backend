"""AI usage attribution context + the reusable recorder.

``AiUsageContext`` is the small object callers attach to an AI call to say *who*
triggered it, *which client* it's for, and *where* in the app it came from.
``record_usage`` is the single write path used by the instrumented
``AnthropicClient`` — it prices the tokens and inserts one ``ai_usage_events``
row on its own short-lived session, committed independently and error-swallowed
so usage logging can never break or roll back with the business transaction
(the tokens were spent regardless of what the caller does next).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.ai.pricing import UsageBreakdown, price
from app.core.config import get_settings
from app.db.session import get_session_factory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AiUsageContext:
    """Attribution for one AI call. Pass it to any AnthropicClient method."""

    feature: str
    user_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None
    meta: dict | None = None


def record_usage(
    *,
    context: AiUsageContext | None,
    provider: str,
    model: str,
    operation: str,
    usage: UsageBreakdown | None,
    status: str = "success",
    error: str | None = None,
    duration_ms: int | None = None,
    request_id: str | None = None,
) -> None:
    """Persist one AI usage event. Never raises."""
    if not get_settings().app.ai_usage_enabled:
        return
    try:
        from app.ai.features import AiFeature
        from app.models.ai_usage import AiUsageEvent

        usage = usage or UsageBreakdown()
        cost = price(model, usage)
        ctx = context or AiUsageContext(feature=AiFeature.UNKNOWN)

        row = AiUsageEvent(
            actor_user_id=ctx.user_id,
            client_id=ctx.client_id,
            feature=ctx.feature or AiFeature.UNKNOWN,
            provider=provider,
            model=model,
            operation=operation,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            total_tokens=usage.total_tokens,
            input_cost=cost.input_cost,
            output_cost=cost.output_cost,
            cache_cost=cost.cache_cost,
            total_cost=cost.total_cost,
            priced=cost.priced,
            status=status,
            error=error,
            duration_ms=duration_ms,
            request_id=request_id,
            meta=ctx.meta,
        )
        with get_session_factory()() as db:
            db.add(row)
            db.commit()
    except Exception as exc:  # logging must never break an AI request
        logger.warning("AI usage recording failed (%s/%s): %s", provider, model, exc)


def usage_from_message(message) -> UsageBreakdown:
    """Extract a token breakdown from an Anthropic Messages response."""
    u = getattr(message, "usage", None)
    if u is None:
        return UsageBreakdown()
    g = lambda name: int(getattr(u, name, 0) or 0)  # noqa: E731
    return UsageBreakdown(
        input_tokens=g("input_tokens"),
        output_tokens=g("output_tokens"),
        cache_write_tokens=g("cache_creation_input_tokens"),
        cache_read_tokens=g("cache_read_input_tokens"),
    )
