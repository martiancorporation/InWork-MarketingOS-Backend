"""The AI command layer's tool-calling orchestrator.

Runs one chat turn: feeds the user's message, recent history, and the tool
registry (``app/ai/tools/registry.py``) to the LLM; executes read tools
inline; and accumulates any write-tool results — already dry-run validated
``StagedOperation``s (or a ``StagedBatch`` of several, for a date-range bulk
tool), see ``ProposalService`` — into a draft. If the turn produced at least
one staged operation, the caller (``AssistantService``) persists them as one
``ChangeProposal`` via ``ProposalService.create_proposal``. Nothing is written
to the database before that, and ``ProposalService.approve`` is the only path
that ever mutates data — this module never calls it, and it isn't in the tool
list the model is given.

Degrades to a deterministic, no-tools reply when the AI provider isn't
configured — the same posture every other ``app/ai`` feature takes.

Read-only enforcement (``UserRole.user``): every write tool is filtered out of
what the model is even offered (``openai_tool_definitions(include_write=...)``)
AND re-checked defensively at dispatch (``_dispatch``) — never trust "the
model wasn't shown the tool" as the only gate for something this sensitive.
``ProposalService.approve``/``reject`` carry the matching backstop on the
approval side, so a read-only user is blocked at every layer: staging,
dispatch, and approval.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from app.ai.features import AiFeature
from app.ai.model_router import model_for
from app.ai.tools.registry import (
    REQUEST_CLARIFICATION,
    TOOLS,
    openai_tool_definitions,
    progress_label_for,
)
from app.core.exceptions import AppError
from app.integrations.llm.base import LLMClient, ToolCall
from app.models.client import Client
from app.models.enums import UserRole
from app.models.user import User
from app.prompts.loader import load_prompt, render
from app.services.proposal_service import StagedBatch, StagedOperation
from app.utils.timezones import client_local_today

logger = logging.getLogger("app.ai.command_agent")

#: Hard cap on LLM<->tool round trips per turn — an unbounded agentic loop
#: against a paid API is both a cost and an availability risk (the same
#: concern the paid-AI routes address with request-rate limiting).
_MAX_ROUNDS = 6
_MAX_HISTORY_MESSAGES = 20
#: The global default (AISettings.max_tokens, 1024) is sized for a single
#: plain completion — too small for a reasoning-capable model doing
#: tool-calling, which can spend a real share of the budget on internal
#: reasoning before ever emitting a tool call or reply. Confirmed live: with
#: the default, a real turn against Claude Sonnet 5 exhausted the budget
#: entirely on reasoning and came back with empty content AND no tool
#: calls — the exact same truncation failure mode already found and fixed
#: for the classifier (see plan_chat_intent.py) and plan_generation.py.
_MAX_TOKENS = 2000

_NOT_CONFIGURED_REPLY = (
    "The AI command assistant isn't configured yet — ask an administrator "
    "to set up the AI provider before natural-language commands will work."
)
_PROVIDER_ERROR_REPLY = "Sorry, I couldn't reach the AI provider just now — please try again."
_ROUND_CAP_REPLY = (
    "I wasn't able to finish working through that in one go — could you narrow down "
    "the request a little?"
)
#: Injected into the system prompt (see command_agent/system.txt's
#: {read_only_notice} slot) only for role == UserRole.user. The actual
#: enforcement is the write tools never being offered/dispatched (below) —
#: this just lets the model explain that plainly instead of getting stuck or
#: hallucinating an apology when it has no matching tool to reach for.
_READ_ONLY_SYSTEM_NOTICE = (
    "IMPORTANT: this user's account has READ-ONLY access on this client. They can chat, "
    "ask questions, and see any information you can look up, but they cannot create, "
    "update, delete, assign, approve, or reject anything — there are no such tools "
    "available in this conversation at all. If they ask you to make any change, do not "
    "attempt it or search for a way around it — just clearly and politely explain that "
    "their account has read-only access and that an admin or manager on this client "
    "needs to make that change, then offer to help with anything read-only instead."
)
#: Returned directly by _dispatch as a defensive backstop if a write tool is
#: ever somehow called for a read-only user (it should never even be offered —
#: see run_turn/run_turn_stream's `openai_tool_definitions(include_write=...)`).
_READ_ONLY_TOOL_ERROR = {
    "error": (
        "This account has read-only access and cannot make this change. "
        "An admin or manager on this client needs to do this instead."
    )
}


@dataclass
class CommandTurnResult:
    reply: str
    operations: list[StagedOperation] = field(default_factory=list)


@dataclass
class CommandStreamEvent:
    """One event from ``CommandAgent.run_turn_stream``.

    ``result`` is set on (and only on) the final event of the stream — every
    prior event is ``"delta"`` (a text token, as the model's own reply is
    generated) or ``"tool_progress"`` (a human-friendly "what I'm doing now"
    label, purely cosmetic). A caller can safely stop looking for more once it
    sees ``type == "result"``.
    """

    type: Literal["delta", "tool_progress", "result"]
    text: str | None = None
    tool_name: str | None = None
    result: CommandTurnResult | None = None


class CommandAgent:
    feature = AiFeature.COMMAND_AGENT

    def __init__(self, db: Session, client_id: uuid.UUID, user: User, ai_client: LLMClient) -> None:
        self.db = db
        self.client_id = client_id
        self.user = user
        self.ai = ai_client

    @property
    def _is_read_only(self) -> bool:
        """``UserRole.user`` is strictly read-only through Ask AI, regardless
        of any per-client capability they hold for the manual UI — see the
        module docstring. Admins and managers are unaffected."""
        return self.user.role == UserRole.user

    async def run_turn(self, content: str, *, history: list[tuple[str, str]]) -> CommandTurnResult:
        if not self.ai.is_configured:
            return CommandTurnResult(reply=_NOT_CONFIGURED_REPLY)

        messages = self._build_messages(content, history)
        tools = openai_tool_definitions(include_write=not self._is_read_only)
        staged: list[StagedOperation] = []

        for _round in range(_MAX_ROUNDS):
            try:
                response = await self.ai.complete_with_tools(
                    messages=messages,
                    tools=tools,
                    model=model_for(self.feature),
                    max_tokens=_MAX_TOKENS,
                )
            except AppError:
                logger.warning("Command agent LLM call failed", exc_info=True)
                return CommandTurnResult(reply=_PROVIDER_ERROR_REPLY, operations=staged)

            if not response.tool_calls:
                return CommandTurnResult(reply=response.content or "", operations=staged)

            messages.append(_assistant_tool_call_message(response.content, response.tool_calls))

            clarification: str | None = None
            for call in response.tool_calls:
                if call.name == REQUEST_CLARIFICATION:
                    clarification = str(call.arguments.get("question") or "Could you clarify that?")
                    messages.append(_tool_message(call.id, {"acknowledged": True}))
                    continue
                result = self._dispatch(call.name, call.arguments)
                if isinstance(result, StagedBatch):
                    staged.extend(result.operations)
                    messages.append(_tool_message(call.id, _staged_batch_tool_result(result)))
                elif isinstance(result, StagedOperation):
                    staged.append(result)
                    messages.append(_tool_message(call.id, _staged_tool_result(result)))
                else:
                    messages.append(_tool_message(call.id, result))

            if clarification is not None:
                return CommandTurnResult(reply=clarification, operations=[])

        # Round cap hit — degrade to a plain reply rather than looping forever.
        return CommandTurnResult(reply=_ROUND_CAP_REPLY, operations=staged)

    async def run_turn_stream(
        self, content: str, *, history: list[tuple[str, str]]
    ) -> AsyncIterator[CommandStreamEvent]:
        """Streaming counterpart to ``run_turn`` — the SSE turn endpoint's
        engine. Yields ``delta`` events as the model's own reply is generated
        and ``tool_progress`` events as tool calls are dispatched; the final
        event is always ``type == "result"``, carrying the exact same
        ``CommandTurnResult`` ``run_turn`` would have returned (same staged
        operations, same reply text) — a caller can persist it identically."""
        if not self.ai.is_configured:
            yield CommandStreamEvent(
                type="result", result=CommandTurnResult(reply=_NOT_CONFIGURED_REPLY)
            )
            return

        messages = self._build_messages(content, history)
        tools = openai_tool_definitions(include_write=not self._is_read_only)
        staged: list[StagedOperation] = []

        for _round in range(_MAX_ROUNDS):
            content_parts: list[str] = []
            round_tool_calls: list[ToolCall] = []
            try:
                async for delta in self.ai.stream_with_tools(
                    messages=messages,
                    tools=tools,
                    model=model_for(self.feature),
                    max_tokens=_MAX_TOKENS,
                ):
                    if delta.text:
                        content_parts.append(delta.text)
                        yield CommandStreamEvent(type="delta", text=delta.text)
                    if delta.tool_call:
                        round_tool_calls.append(delta.tool_call)
            except AppError:
                logger.warning("Command agent streaming LLM call failed", exc_info=True)
                yield CommandStreamEvent(
                    type="result",
                    result=CommandTurnResult(reply=_PROVIDER_ERROR_REPLY, operations=staged),
                )
                return

            round_content = "".join(content_parts) or None
            if not round_tool_calls:
                yield CommandStreamEvent(
                    type="result",
                    result=CommandTurnResult(reply=round_content or "", operations=staged),
                )
                return

            messages.append(_assistant_tool_call_message(round_content, round_tool_calls))

            clarification: str | None = None
            for call in round_tool_calls:
                if call.name == REQUEST_CLARIFICATION:
                    clarification = str(call.arguments.get("question") or "Could you clarify that?")
                    messages.append(_tool_message(call.id, {"acknowledged": True}))
                    continue
                yield CommandStreamEvent(
                    type="tool_progress", tool_name=progress_label_for(call.name)
                )
                result = self._dispatch(call.name, call.arguments)
                if isinstance(result, StagedBatch):
                    staged.extend(result.operations)
                    messages.append(_tool_message(call.id, _staged_batch_tool_result(result)))
                elif isinstance(result, StagedOperation):
                    staged.append(result)
                    messages.append(_tool_message(call.id, _staged_tool_result(result)))
                else:
                    messages.append(_tool_message(call.id, result))

            if clarification is not None:
                yield CommandStreamEvent(
                    type="result", result=CommandTurnResult(reply=clarification, operations=[])
                )
                return

        yield CommandStreamEvent(
            type="result", result=CommandTurnResult(reply=_ROUND_CAP_REPLY, operations=staged)
        )

    def _build_messages(self, content: str, history: list[tuple[str, str]]) -> list[dict]:
        client = self.db.get(Client, self.client_id)
        system = render(
            load_prompt("command_agent/system.txt"),
            {
                "client_name": client.name if client else "this client",
                "today": client_local_today(client.timezone if client else None).isoformat(),
                "read_only_notice": _READ_ONLY_SYSTEM_NOTICE if self._is_read_only else "",
            },
        )
        messages: list[dict] = [{"role": "system", "content": system}]
        for role, text in history[-_MAX_HISTORY_MESSAGES:]:
            messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": content})
        return messages

    def _dispatch(self, name: str, arguments: dict) -> dict | StagedOperation | StagedBatch:
        spec = TOOLS.get(name)
        if spec is None or spec.handler is None:
            return {"error": f"Unknown tool '{name}'."}
        if spec.kind == "write" and self._is_read_only:
            # Defensive backstop — write tools aren't offered to a read-only
            # user at all (see run_turn/run_turn_stream), so this should never
            # actually trigger, but never trust "the model won't call a tool
            # it wasn't shown" as the only gate for something this sensitive.
            return _READ_ONLY_TOOL_ERROR
        try:
            return spec.handler(self.db, self.client_id, self.user, **arguments)
        except AppError as exc:
            # Surfaced back to the model as a tool result, not raised — lets it
            # adapt (e.g. ask for clarification) instead of crashing the turn.
            return {"error": exc.message}
        except (TypeError, ValueError) as exc:
            # A malformed/unexpected argument from the model — same treatment.
            return {"error": f"Invalid arguments for {name}: {exc}"}


def _assistant_tool_call_message(content: str | None, tool_calls: list[ToolCall]) -> dict:
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in tool_calls
        ],
    }


def _staged_tool_result(op: StagedOperation) -> dict:
    return {
        "staged": True,
        "summary": (
            f"Drafted: {op.entity_label or op.entity_type} (pending human approval, not yet applied)"
        ),
    }


def _staged_batch_tool_result(batch: StagedBatch) -> dict:
    result = {
        "staged": True,
        "summary": (
            f"Drafted {len(batch.operations)} change(s) across the matched items "
            "(pending human approval, not yet applied — all in the same proposal)."
        ),
        "staged_count": len(batch.operations),
    }
    if batch.skipped:
        result["skipped"] = batch.skipped
        result["skipped_count"] = len(batch.skipped)
    return result


def _tool_message(call_id: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)}
