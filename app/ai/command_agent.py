"""The AI command layer's tool-calling orchestrator.

Runs one chat turn: feeds the user's message, recent history, and the tool
registry (``app/ai/tools/registry.py``) to the LLM; executes read tools
inline; and accumulates any write-tool results — already dry-run validated
``StagedOperation``s, see ``ProposalService`` — into a draft. If the turn
produced at least one staged operation, the caller (``AssistantService``)
persists them as one ``ChangeProposal`` via ``ProposalService.create_proposal``.
Nothing is written to the database before that, and ``ProposalService.approve``
is the only path that ever mutates data — this module never calls it, and it
isn't in the tool list the model is given.

Degrades to a deterministic, no-tools reply when the AI provider isn't
configured — the same posture every other ``app/ai`` feature takes.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from app.ai.tools.registry import (
    REQUEST_CLARIFICATION,
    TOOLS,
    openai_tool_definitions,
    progress_label_for,
)
from app.core.exceptions import AppError
from app.integrations.llm.base import LLMClient, ToolCall
from app.models.client import Client
from app.models.user import User
from app.prompts.loader import load_prompt, render
from app.services.proposal_service import StagedOperation
from app.utils.timezones import client_local_today

logger = logging.getLogger("app.ai.command_agent")

#: Hard cap on LLM<->tool round trips per turn — an unbounded agentic loop
#: against a paid API is both a cost and an availability risk (the same
#: concern the paid-AI routes address with request-rate limiting).
_MAX_ROUNDS = 6
_MAX_HISTORY_MESSAGES = 20

_NOT_CONFIGURED_REPLY = (
    "The AI command assistant isn't configured yet — ask an administrator "
    "to set up the AI provider before natural-language commands will work."
)
_PROVIDER_ERROR_REPLY = "Sorry, I couldn't reach the AI provider just now — please try again."
_ROUND_CAP_REPLY = (
    "I wasn't able to finish working through that in one go — could you narrow down "
    "the request a little?"
)


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
    def __init__(self, db: Session, client_id: uuid.UUID, user: User, ai_client: LLMClient) -> None:
        self.db = db
        self.client_id = client_id
        self.user = user
        self.ai = ai_client

    async def run_turn(self, content: str, *, history: list[tuple[str, str]]) -> CommandTurnResult:
        if not self.ai.is_configured:
            return CommandTurnResult(reply=_NOT_CONFIGURED_REPLY)

        messages = self._build_messages(content, history)
        tools = openai_tool_definitions()
        staged: list[StagedOperation] = []

        for _round in range(_MAX_ROUNDS):
            try:
                response = await self.ai.complete_with_tools(messages=messages, tools=tools)
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
                if isinstance(result, StagedOperation):
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
            yield CommandStreamEvent(type="result", result=CommandTurnResult(reply=_NOT_CONFIGURED_REPLY))
            return

        messages = self._build_messages(content, history)
        tools = openai_tool_definitions()
        staged: list[StagedOperation] = []

        for _round in range(_MAX_ROUNDS):
            content_parts: list[str] = []
            round_tool_calls: list[ToolCall] = []
            try:
                async for delta in self.ai.stream_with_tools(messages=messages, tools=tools):
                    if delta.text:
                        content_parts.append(delta.text)
                        yield CommandStreamEvent(type="delta", text=delta.text)
                    if delta.tool_call:
                        round_tool_calls.append(delta.tool_call)
            except AppError:
                logger.warning("Command agent streaming LLM call failed", exc_info=True)
                yield CommandStreamEvent(
                    type="result", result=CommandTurnResult(reply=_PROVIDER_ERROR_REPLY, operations=staged)
                )
                return

            round_content = "".join(content_parts) or None
            if not round_tool_calls:
                yield CommandStreamEvent(
                    type="result", result=CommandTurnResult(reply=round_content or "", operations=staged)
                )
                return

            messages.append(_assistant_tool_call_message(round_content, round_tool_calls))

            clarification: str | None = None
            for call in round_tool_calls:
                if call.name == REQUEST_CLARIFICATION:
                    clarification = str(call.arguments.get("question") or "Could you clarify that?")
                    messages.append(_tool_message(call.id, {"acknowledged": True}))
                    continue
                yield CommandStreamEvent(type="tool_progress", tool_name=progress_label_for(call.name))
                result = self._dispatch(call.name, call.arguments)
                if isinstance(result, StagedOperation):
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
            },
        )
        messages: list[dict] = [{"role": "system", "content": system}]
        for role, text in history[-_MAX_HISTORY_MESSAGES:]:
            messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": content})
        return messages

    def _dispatch(self, name: str, arguments: dict) -> dict | StagedOperation:
        spec = TOOLS.get(name)
        if spec is None or spec.handler is None:
            return {"error": f"Unknown tool '{name}'."}
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


def _tool_message(call_id: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)}
