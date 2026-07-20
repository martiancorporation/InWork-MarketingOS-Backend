"""Project AI assistant — "Ask AI about this project".

A per-client conversational agent grounded in the client's intelligence context
(directive preamble + capability flags) and its RAG knowledge store, so answers
reflect that client's brand, goals, and compliance rules. Extends ``ClientAgent``
so the client's rule preamble is always prepended and usage is attributed.

Graceful degradation: when Anthropic is unconfigured or the call fails, it returns
a deterministic, source-grounded reply instead of raising — the same house stance
as every other AI feature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.ai.features import AiFeature
from app.prompts.loader import load_prompt, render
from app.services.intelligence.client_agent import ClientAgent

logger = logging.getLogger("app.ai.assistant")

_MAX_SNIPPETS = 6  # retrieved RAG chunks fed as grounding
_MAX_HISTORY = 10  # recent turns fed back for continuity


@dataclass
class AssistantStreamPrep:
    """Everything a streamed answer needs, computed while the DB session is open.

    ``system``/``prompt`` are None when Claude is unconfigured — the caller then
    streams ``fallback`` instead of calling the provider.
    """

    snippets: list[str]
    fallback: str
    system: str | None
    prompt: str | None


class ProjectAssistantAgent(ClientAgent):
    feature = AiFeature.PROJECT_AI

    async def answer(
        self, question: str, *, history: list[tuple[str, str]] | None = None
    ) -> tuple[str, list[str]]:
        """Answer one question. Returns ``(answer_text, source_snippets)``.

        ``history`` is the prior turns as ``[(role, content), ...]`` (chronological).
        """
        snippets = self.retrieve(question, top_k=_MAX_SNIPPETS)
        if not self.ai.is_configured:
            return self._fallback(snippets), snippets

        context_block = (
            "\n".join(f"- {s}" for s in snippets)
            or "(no indexed project knowledge matched this question)"
        )
        system = self.system_prompt(load_prompt("assistant/system.txt"))
        prompt = render(
            load_prompt("assistant/user_template.txt"),
            {
                "question": question,
                "context": context_block,
                "history": _format_history(history or []),
            },
        )
        try:
            raw = await self.ai.complete(system=system, prompt=prompt)
        except Exception:  # transient API error — degrade, never 500 the chat
            logger.warning(
                "Project assistant completion failed for client %s",
                self.client_id,
                exc_info=True,
            )
            return self._fallback(snippets), snippets
        return (raw.strip() or self._fallback(snippets)), snippets

    def prepare_stream(
        self, question: str, *, history: list[tuple[str, str]] | None = None
    ) -> AssistantStreamPrep:
        """Do all DB/RAG work (retrieval + prompt build) up front so the streaming
        step touches only the AI provider — call this while the request's DB
        session is still open, then stream from ``AnthropicClient.stream``."""
        snippets = self.retrieve(question, top_k=_MAX_SNIPPETS)
        fallback = self._fallback(snippets)
        if not self.ai.is_configured:
            return AssistantStreamPrep(snippets, fallback, None, None)

        context_block = (
            "\n".join(f"- {s}" for s in snippets)
            or "(no indexed project knowledge matched this question)"
        )
        system = self.system_prompt(load_prompt("assistant/system.txt"))
        prompt = render(
            load_prompt("assistant/user_template.txt"),
            {
                "question": question,
                "context": context_block,
                "history": _format_history(history or []),
            },
        )
        return AssistantStreamPrep(snippets, fallback, system, prompt)

    def _fallback(self, snippets: list[str]) -> str:
        if snippets:
            joined = "\n".join(f"- {s}" for s in snippets[:3])
            return (
                "AI responses aren't configured in this environment yet, so here is the "
                "most relevant project knowledge I found for your question:\n\n" + joined
            )
        return (
            "AI responses aren't configured in this environment yet, and I couldn't find "
            "indexed project knowledge for that question. Add sources or build this client's "
            "intelligence profile, then ask again."
        )


def _format_history(history: list[tuple[str, str]]) -> str:
    turns = history[-_MAX_HISTORY:]
    if not turns:
        return "(no earlier messages)"
    return "\n".join(f"{role.capitalize()}: {content}" for role, content in turns)
