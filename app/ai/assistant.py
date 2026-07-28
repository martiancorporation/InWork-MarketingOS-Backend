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

from app.ai.attachments import AttachmentBundle
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
        self,
        question: str,
        *,
        history: list[tuple[str, str]] | None = None,
        attachments: AttachmentBundle | None = None,
    ) -> tuple[str, list[str]]:
        """Answer one question. Returns ``(answer_text, source_snippets)``.

        ``history`` is the prior turns as ``[(role, content), ...]`` (chronological).
        ``attachments`` carries this turn's files: document text goes into the prompt,
        images are sent as vision blocks.
        """
        snippets = self.retrieve(question, top_k=_MAX_SNIPPETS)
        if not self.ai.is_configured:
            return self._fallback(snippets, attachments), snippets

        context_block = (
            "\n".join(f"- {s}" for s in snippets)
            or "(no indexed project knowledge matched this question)"
        )
        system = self.system_prompt(load_prompt("assistant/system.txt"))
        prompt = render(
            load_prompt("assistant/user_template.txt"),
            {
                # An attachment-only turn has no text; say so rather than rendering a
                # blank line the model has to guess at.
                "question": question.strip() or "(no question text — see the attachments)",
                "context": context_block,
                "history": _format_history(history or []),
                "attachments": attachments.as_prompt_block() if attachments else "(none)",
            },
        )
        images = attachments.images if attachments else []
        try:
            if images:
                raw = await self.ai.complete_with_images(
                    system=system, prompt=prompt, images=images
                )
            else:
                raw = await self.ai.complete(system=system, prompt=prompt)
        except Exception:  # transient API error — degrade, never 500 the chat
            logger.warning(
                "Project assistant completion failed for client %s",
                self.client_id,
                exc_info=True,
            )
            return self._fallback(snippets, attachments), snippets
        return (raw.strip() or self._fallback(snippets, attachments)), snippets

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
                # The streaming route rejects attachments (the provider's stream API
                # is text-only), so this is always empty here.
                "attachments": "(none)",
            },
        )
        return AssistantStreamPrep(snippets, fallback, system, prompt)

    def _fallback(
        self, snippets: list[str], attachments: AttachmentBundle | None = None
    ) -> str:
        # Say the files arrived even though nothing could read them — otherwise an
        # operator who attached a report gets a generic "not configured" reply and
        # reasonably assumes the upload itself failed.
        note = ""
        if attachments and attachments.parts:
            names = ", ".join(p.filename for p in attachments.parts)
            note = f"\n\nI did receive your attachment(s): {names}."
        if snippets:
            joined = "\n".join(f"- {s}" for s in snippets[:3])
            return (
                "AI responses aren't configured in this environment yet, so here is the "
                "most relevant project knowledge I found for your question:\n\n" + joined + note
            )
        return (
            "AI responses aren't configured in this environment yet, and I couldn't find "
            "indexed project knowledge for that question. Add sources or build this client's "
            "intelligence profile, then ask again." + note
        )


def _format_history(history: list[tuple[str, str]]) -> str:
    turns = history[-_MAX_HISTORY:]
    if not turns:
        return "(no earlier messages)"
    return "\n".join(f"{role.capitalize()}: {content}" for role, content in turns)
