"""Platform-wide AI assistant ("Ask AI about my portfolio").

Unlike ``ProjectAssistantAgent`` (one client), this agent reasons across the set
of clients the current user is allowed to see — the whole platform for an admin,
only the assigned clients for everyone else. Access scoping is decided by the
caller (the service) and handed in as the ``platform_facts`` fact sheet; the agent
never widens it.

Graceful degradation: when Anthropic is unconfigured or the call fails, it returns
a deterministic, fact-grounded summary instead of raising — same house stance as
every other AI feature. All portfolio data is DATA, never instructions.
"""

from __future__ import annotations

import logging

from app.ai.features import AiFeature
from app.integrations.anthropic.client import AnthropicClient
from app.prompts.loader import load_prompt, render

logger = logging.getLogger("app.ai.global_assistant")

_MAX_HISTORY = 10


class GlobalAssistantAgent:
    feature = AiFeature.ASSISTANT

    def __init__(self, ai_client: AnthropicClient | None = None) -> None:
        self._client = ai_client or AnthropicClient()

    async def answer(
        self,
        question: str,
        *,
        platform_facts: str,
        scope_label: str,
        history: list[tuple[str, str]] | None = None,
    ) -> str:
        """Answer one platform-level question grounded in ``platform_facts``."""
        if not self._client.is_configured:
            return self._fallback(platform_facts, scope_label)

        system = load_prompt("global_assistant/system.txt")
        prompt = render(
            load_prompt("global_assistant/user_template.txt"),
            {
                "question": question,
                "scope": scope_label,
                "facts": platform_facts or "(no accessible client data)",
                "history": _format_history(history or []),
            },
        )
        try:
            raw = await self._client.complete(system=system, prompt=prompt, max_tokens=1500)
        except Exception:  # transient API error — degrade, never 500 the chat
            logger.warning("Global assistant completion failed", exc_info=True)
            return self._fallback(platform_facts, scope_label)
        return raw.strip() or self._fallback(platform_facts, scope_label)

    def _fallback(self, platform_facts: str, scope_label: str) -> str:
        if platform_facts.strip():
            return (
                "AI responses aren't configured in this environment yet, so here is a "
                f"summary of the {scope_label} you can access:\n\n{platform_facts}"
            )
        return (
            "AI responses aren't configured in this environment yet, and there is no "
            "accessible client data to summarize. Get assigned to a client, then ask again."
        )


def _format_history(history: list[tuple[str, str]]) -> str:
    turns = history[-_MAX_HISTORY:]
    if not turns:
        return "(no earlier messages)"
    return "\n".join(f"{role.capitalize()}: {content}" for role, content in turns)
