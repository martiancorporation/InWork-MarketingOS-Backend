"""Fast pre-check for the Ask AI chat: does this message want a content plan
created, and if so, do we already know the date range?

Runs ahead of every chat turn (see ``AssistantService.ask``/``begin_stream``).
Deliberately a separate, cheap classification call rather than folding this
into ``ProjectAssistantAgent``'s own completion — a small, focused prompt is
more reliable at "is this asking to create content + what dates" than one
prompt juggling persona, RAG grounding, AND structured-output detection at
once, and it keeps the existing conversational path (streamed prose) totally
unaffected when the answer is "no." Never raises: any failure or an
unconfigured AI provider degrades to "not a plan request," so a normal chat
reply always still happens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from app.ai.features import AiFeature
from app.ai.model_router import model_for
from app.ai.parsers import parse_json_object
from app.prompts.loader import load_prompt, render
from app.services.intelligence.client_agent import ClientAgent

logger = logging.getLogger("app.ai.plan_chat_intent")

_MAX_HISTORY_TURNS = 6  # only recent turns matter for resolving "this month" etc.
_DEFAULT_CLARIFYING_QUESTION = "Sure — what date range should this cover?"


@dataclass(frozen=True)
class PlanChatIntent:
    wants_content_plan: bool
    ready: bool
    start_date: date | None
    end_date: date | None
    clarifying_question: str | None
    #: Anything the manager said, anywhere in the conversation, about what the
    #: content should actually say/look like (a CTA to keep, a topic, a tone)
    #: — forwarded as the generation prompt so it isn't silently dropped just
    #: because it was mentioned in an earlier turn than the one with the date.
    content_instructions: str | None = None
    #: A person named anywhere in the conversation to assign the generated
    #: content to, verbatim (resolved against this client's team separately —
    #: never trusted as a real id here).
    assignee_hint: str | None = None


_NOT_A_PLAN_REQUEST = PlanChatIntent(
    wants_content_plan=False, ready=False, start_date=None, end_date=None, clarifying_question=None
)


class PlanChatIntentAgent(ClientAgent):
    feature = AiFeature.PLAN_CHAT_INTENT

    async def classify(
        self, message: str, *, history: list[tuple[str, str]], today: date
    ) -> PlanChatIntent:
        if not message.strip() or not self.ai.is_configured:
            return _NOT_A_PLAN_REQUEST

        user_prompt = render(
            load_prompt("plan_chat_intent/user_template.txt"),
            {
                "today": today.isoformat(),
                "history": _format_history(history),
                "message": message.strip(),
            },
        )
        try:
            raw = await self.ai.complete(
                system=self.system_prompt(load_prompt("plan_chat_intent/system.txt")),
                prompt=user_prompt,
                # The JSON payload itself is tiny, but the routed model may
                # spend tokens on chain-of-thought before emitting it — with a
                # too-small budget the response gets cut off mid-JSON and
                # silently fails to parse (confirmed live: qwen3.7-flash used
                # every token of a 400 cap and produced truncated, unparseable
                # output). Sized like plan_generation.py's single-item budget.
                max_tokens=1500,
                model=model_for(self.feature),
            )
        except Exception:
            logger.warning(
                "Plan chat intent check failed for client %s", self.client_id, exc_info=True
            )
            return _NOT_A_PLAN_REQUEST

        payload = parse_json_object(raw)
        if not payload:
            return _NOT_A_PLAN_REQUEST
        return _parse_intent(payload, today)


def _parse_intent(payload: dict, today: date) -> PlanChatIntent:
    if not payload.get("wants_content_plan"):
        return _NOT_A_PLAN_REQUEST

    start = _parse_date_on_or_after(payload.get("start_date"), today)
    end = _parse_date_on_or_after(payload.get("end_date"), today)
    if payload.get("ready") and start is not None and end is not None and end >= start:
        return PlanChatIntent(
            wants_content_plan=True,
            ready=True,
            start_date=start,
            end_date=end,
            clarifying_question=None,
            content_instructions=_clean_text(payload.get("content_instructions")),
            assignee_hint=_clean_text(payload.get("assignee_hint")),
        )

    question = _clean_text(payload.get("clarifying_question")) or _DEFAULT_CLARIFYING_QUESTION
    return PlanChatIntent(
        wants_content_plan=True,
        ready=False,
        start_date=None,
        end_date=None,
        clarifying_question=question,
    )


def _parse_date_on_or_after(raw: object, today: date) -> date | None:
    """Never trust the model's date alone — a resolved range that's somehow
    still before today (a model mistake, e.g. resolving "this month" without
    noticing today is mid-month) is treated as unparseable rather than passed
    through, matching the same "never before today" guarantee as the actual
    generation step (app/ai/plan_generation.py)."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed >= today else None


def _clean_text(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text[:500] if text else None


def _format_history(history: list[tuple[str, str]]) -> str:
    turns = history[-_MAX_HISTORY_TURNS:]
    if not turns:
        return "(no earlier messages)"
    return "\n".join(f"{role.capitalize()}: {content}" for role, content in turns)
