"""AI content-calendar generation — the client's top-requested feature: a
manager asks in chat ("create a content calendar for Tony's Garage this
month") and gets back a structured, guardrail-respecting set of draft items to
review, assign, and track.

Mirrors ``ContentReviewAgent``: grounded in the client's rule preamble
(compliance + brand voice, via ``ClientAgent``'s intelligence context),
structured JSON output (``app.ai.parsers.parse_json_object``), deterministic
fallback when the AI provider is unconfigured or the call fails — the planning
loop the client cares about most must never dead-end on a 500 or a blank
result.
"""

from __future__ import annotations

import calendar as _calendar
import logging
from dataclasses import dataclass
from datetime import date

from app.ai.features import AiFeature
from app.ai.model_router import model_for
from app.ai.parsers import parse_json_object
from app.models.enums import SocialPlatform, TaskCategory
from app.prompts.loader import load_prompt, render
from app.services.intelligence.client_agent import ClientAgent

logger = logging.getLogger("app.ai.plan_generation")

_VALID_PLATFORMS = {p.value for p in SocialPlatform}
_VALID_CATEGORIES = {c.value for c in TaskCategory}
_DEFAULT_PLATFORM = SocialPlatform.instagram.value
_DEFAULT_CATEGORY = TaskCategory.content.value
_MAX_MONTH_ITEMS = 40  # a sane cap on one AI response, regardless of what the model returns


@dataclass
class ProposedItem:
    """One AI-proposed (or fallback) calendar item, already validated/clamped."""

    title: str
    event_date: date
    platform: str
    category: str
    content_format: str | None
    caption: str | None
    hashtags: str | None
    suggested_role: str | None


class PlanGenerationAgent(ClientAgent):
    feature = AiFeature.PLAN_GENERATION

    async def generate_month(
        self, prompt: str, *, year: int, month: int, existing_titles: list[str]
    ) -> list[ProposedItem]:
        """Propose a month of calendar items for ``prompt``. Never raises — falls
        back to a small deterministic placeholder plan on any failure."""
        if not self.ai.is_configured:
            return _fallback_month(year, month)

        month_label = date(year, month, 1).strftime("%B %Y")
        existing = "\n".join(f"- {t}" for t in existing_titles[:100]) or "(none yet)"
        user_prompt = render(
            load_prompt("plan_generation/user_template.txt"),
            {
                "prompt": prompt.strip(),
                "month": f"{year:04d}-{month:02d}",
                "month_label": month_label,
                "existing_items": existing,
            },
        )
        try:
            raw = await self.ai.complete(
                system=self.system_prompt(load_prompt("plan_generation/system.txt")),
                prompt=user_prompt,
                model=model_for(self.feature),
            )
        except Exception:
            logger.warning("Plan generation failed for client %s", self.client_id, exc_info=True)
            return _fallback_month(year, month)

        payload = parse_json_object(raw) or {}
        raw_items = payload.get("items")
        items = _parse_items(raw_items, year, month) if isinstance(raw_items, list) else []
        return items or _fallback_month(year, month)

    async def regenerate_item(
        self,
        *,
        current_title: str,
        current_caption: str | None,
        event_date: date,
        instructions: str,
        reason: str,
    ) -> ProposedItem:
        """Replace exactly one already-scheduled item per a real-time change
        request. Never raises — falls back to a deterministic placeholder that
        still records the manager's instructions in the title."""
        if not self.ai.is_configured:
            return _fallback_item(event_date, instructions)

        user_prompt = render(
            load_prompt("plan_generation/single_item_user_template.txt"),
            {
                "event_date": event_date.isoformat(),
                "current_title": current_title,
                "current_caption": current_caption or "(none)",
                "reason": reason.strip(),
                "instructions": instructions.strip(),
            },
        )
        try:
            raw = await self.ai.complete(
                system=self.system_prompt(load_prompt("plan_generation/single_item_system.txt")),
                prompt=user_prompt,
                model=model_for(self.feature),
            )
        except Exception:
            logger.warning(
                "Single-item plan regeneration failed for client %s", self.client_id, exc_info=True
            )
            return _fallback_item(event_date, instructions)

        payload = parse_json_object(raw) or {}
        item = _parse_one_item(payload, event_date)
        return item or _fallback_item(event_date, instructions)


def _clean_str(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:max_length] if text else None


def _parse_one_item(payload: dict, event_date: date) -> ProposedItem | None:
    title = _clean_str(payload.get("title"), max_length=200)
    if not title:
        return None
    platform = payload.get("platform")
    platform = platform if platform in _VALID_PLATFORMS else _DEFAULT_PLATFORM
    category = payload.get("category")
    category = category if category in _VALID_CATEGORIES else _DEFAULT_CATEGORY
    return ProposedItem(
        title=title,
        event_date=event_date,
        platform=platform,
        category=category,
        content_format=_clean_str(payload.get("content_format"), max_length=30),
        caption=_clean_str(payload.get("caption"), max_length=100_000),
        hashtags=_clean_str(payload.get("hashtags"), max_length=2_000),
        suggested_role=_clean_str(payload.get("suggested_role"), max_length=80),
    )


def _parse_items(raw_items: list, year: int, month: int) -> list[ProposedItem]:
    last_day = _calendar.monthrange(year, month)[1]
    items: list[ProposedItem] = []
    for raw in raw_items[:_MAX_MONTH_ITEMS]:
        if not isinstance(raw, dict):
            continue
        event_date = _parse_and_clamp_date(raw.get("event_date"), year, month, last_day)
        item = _parse_one_item(raw, event_date)
        if item is not None:
            items.append(item)
    return items


def _parse_and_clamp_date(raw: object, year: int, month: int, last_day: int) -> date:
    """Best-effort parse of a model-supplied date, clamped into the target
    month — a model that drifts outside the requested month (or returns
    garbage) must never produce a task the calendar can't place correctly."""
    if isinstance(raw, str):
        try:
            parsed = date.fromisoformat(raw)
            if parsed.year == year and parsed.month == month:
                return parsed
        except ValueError:
            pass
    return date(year, month, min(last_day, 15))  # mid-month default when unparseable


def _fallback_month(year: int, month: int) -> list[ProposedItem]:
    """Deterministic placeholder plan when AI is unconfigured or fails — a
    handful of evenly-spaced draft slots the manager can edit by hand, so the
    feature never dead-ends on an empty result."""
    last_day = _calendar.monthrange(year, month)[1]
    days = list(range(3, last_day + 1, 5))[:6] or [1]
    return [
        ProposedItem(
            title=f"Content idea #{i + 1} (draft — AI unavailable, edit before approving)",
            event_date=date(year, month, d),
            platform=_DEFAULT_PLATFORM,
            category=_DEFAULT_CATEGORY,
            content_format="static",
            caption=None,
            hashtags=None,
            suggested_role="content creator",
        )
        for i, d in enumerate(days)
    ]


def _fallback_item(event_date: date, instructions: str) -> ProposedItem:
    return ProposedItem(
        title=f"Revised per request (draft — AI unavailable, edit before approving): {instructions[:100]}",
        event_date=event_date,
        platform=_DEFAULT_PLATFORM,
        category=_DEFAULT_CATEGORY,
        content_format=None,
        caption=None,
        hashtags=None,
        suggested_role=None,
    )
