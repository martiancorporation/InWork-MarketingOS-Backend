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

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from app.ai.features import AiFeature
from app.ai.model_router import model_for
from app.ai.parsers import parse_json_object
from app.models.client import Client
from app.models.enums import SocialPlatform, TaskCategory
from app.prompts.loader import load_prompt, render
from app.services.intelligence.client_agent import ClientAgent

logger = logging.getLogger("app.ai.plan_generation")

_VALID_PLATFORMS = {p.value for p in SocialPlatform}
_VALID_CATEGORIES = {c.value for c in TaskCategory}
_DEFAULT_PLATFORM = SocialPlatform.instagram.value
_DEFAULT_CATEGORY = TaskCategory.content.value
_MAX_RANGE_ITEMS = 40  # a sane cap on one AI response, regardless of what the model returns
_NO_PROMPT_TEXT = (
    "(No specific instructions were given — use the client's brand voice, goals, "
    "and active campaign strategy above to decide what to post.)"
)


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

    async def generate_range(
        self,
        prompt: str,
        *,
        start_date: date,
        end_date: date,
        existing_titles: list[str],
        today: date | None = None,
    ) -> list[ProposedItem]:
        """Propose calendar items for ``[start_date, end_date]`` (inclusive).

        ``start_date`` is the caller's responsibility to have already floored at
        "today" when relevant (see ``PlanGenerationService.propose_month`` /
        ``propose_range``) — this agent additionally never accepts a model-
        returned date before ``start_date`` (``_parse_and_clamp_date``), so a
        model that ignores the instruction still can't produce a past-dated
        item. Never raises — falls back to a small deterministic placeholder
        plan on any failure.
        """
        if not self.ai.is_configured:
            return _fallback_range(start_date, end_date)

        range_label = _format_range_label(start_date, end_date)
        existing = "\n".join(f"- {t}" for t in existing_titles[:100]) or "(none yet)"
        user_prompt = render(
            load_prompt("plan_generation/user_template.txt"),
            {
                "prompt": prompt.strip() or _NO_PROMPT_TEXT,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "range_label": range_label,
                "today": (today or start_date).isoformat(),
                "existing_items": existing,
                "brand_snapshot": self._brand_snapshot(),
            },
        )
        try:
            raw = await self.ai.complete(
                system=self.system_prompt(load_prompt("plan_generation/system.txt")),
                prompt=user_prompt,
                # A range can hold up to _MAX_RANGE_ITEMS structured items (title,
                # caption, hashtags, format each), and the routed model may be a
                # reasoning model that spends tokens on chain-of-thought before it
                # emits any JSON — the 1024-token global default truncates before
                # real content comes out. Sized like app/ai/summary.py's 8000.
                max_tokens=8000,
                model=model_for(self.feature),
            )
        except Exception:
            logger.warning("Plan generation failed for client %s", self.client_id, exc_info=True)
            return _fallback_range(start_date, end_date)

        payload = parse_json_object(raw) or {}
        raw_items = payload.get("items")
        items = _parse_items(raw_items, start_date, end_date) if isinstance(raw_items, list) else []
        return items or _fallback_range(start_date, end_date)

    def _brand_snapshot(self) -> str:
        """A live read of this client's brand/goal fields, straight off the
        row — not the versioned directive preamble, which only reflects
        whatever the async intelligence pipeline last finished processing.

        A brand/goals edit saved seconds ago is picked up immediately here,
        even if that edit's rebuild job hasn't run (or finished) yet. See
        ``ClientAgent.system_prompt``/``self.context.preamble`` for the
        (still-included) versioned rules this supplements, not replaces.
        """
        client = self.db.get(Client, self.client_id)
        if client is None:
            return "(none)"
        fields = {
            "Industry": client.industry,
            "Business type": client.business_type,
            "About the brand": client.about_brand,
            "Brand voice": client.brand_voice,
            "Goals": client.goals,
            "Target markets": client.markets,
        }
        lines = [f"- {label}: {value}" for label, value in fields.items() if value]
        return "\n".join(lines) or "(none on file)"

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
                # One item is much smaller than a full month, but still needs
                # headroom for a reasoning model's chain-of-thought before the
                # JSON answer — see the max_tokens comment in generate_month.
                max_tokens=2000,
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


def _parse_items(raw_items: list, start_date: date, end_date: date) -> list[ProposedItem]:
    items: list[ProposedItem] = []
    for raw in raw_items[:_MAX_RANGE_ITEMS]:
        if not isinstance(raw, dict):
            continue
        event_date = _parse_and_clamp_date(raw.get("event_date"), start_date, end_date)
        item = _parse_one_item(raw, event_date)
        if item is not None:
            items.append(item)
    return items


def _parse_and_clamp_date(raw: object, start_date: date, end_date: date) -> date:
    """Best-effort parse of a model-supplied date, clamped into
    ``[start_date, end_date]`` — a model that drifts outside the requested
    range (or ignores the "never before start_date" instruction, or returns
    garbage) must never produce a task the calendar can't place correctly, and
    must never land before ``start_date`` (the caller's already-floored-at-today
    boundary — this is the actual bug fix, not just cosmetic clamping)."""
    if isinstance(raw, str):
        try:
            parsed = date.fromisoformat(raw)
            if start_date <= parsed <= end_date:
                return parsed
        except ValueError:
            pass
    midpoint_offset = (end_date - start_date).days // 2
    return start_date + timedelta(days=midpoint_offset)  # mid-range default when unparseable


def _format_range_label(start_date: date, end_date: date) -> str:
    """Human-readable range label, e.g. 'September 15–30, 2026' or
    'September 20, 2026 – October 5, 2026'. Avoids strftime's non-portable
    '%-d' (Linux/macOS only) by pulling ``.day`` directly."""
    if start_date.year == end_date.year and start_date.month == end_date.month:
        return f"{start_date.strftime('%B')} {start_date.day}–{end_date.day}, {end_date.year}"
    return (
        f"{start_date.strftime('%B')} {start_date.day}, {start_date.year} – "
        f"{end_date.strftime('%B')} {end_date.day}, {end_date.year}"
    )


def _fallback_range(start_date: date, end_date: date) -> list[ProposedItem]:
    """Deterministic placeholder plan when AI is unconfigured or fails — a
    handful of evenly-spaced draft slots the manager can edit by hand, so the
    feature never dead-ends on an empty result. Spaced across the actual
    requested range (never before ``start_date``), not a fixed calendar month."""
    span_days = (end_date - start_date).days + 1
    step = 5 if span_days > 10 else max(1, span_days // 6 or 1)
    offsets = list(range(0, span_days, step))[:6] or [0]
    return [
        ProposedItem(
            title=f"Content idea #{i + 1} (draft — AI unavailable, edit before approving)",
            event_date=start_date + timedelta(days=offset),
            platform=_DEFAULT_PLATFORM,
            category=_DEFAULT_CATEGORY,
            content_format="static",
            caption=None,
            hashtags=None,
            suggested_role="content creator",
        )
        for i, offset in enumerate(offsets)
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
