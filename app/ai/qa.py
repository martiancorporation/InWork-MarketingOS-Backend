"""Cross-provider QA layer.

One provider (Anthropic) generates content; a DIFFERENT provider independently
reviews it, so single-vendor bias is countered. The reviewer returns a structured
verdict (ok / concerns + notes) grounded in the same client context and facts the
generator used.

Graceful degradation is the whole point of the ``not_reviewed`` status: when QA
is disabled (``AI_QA_ENABLED`` off) or the QA provider is unconfigured, ``review``
returns a clean ``not_reviewed`` passthrough — never an error, never a block. The
QA provider is chosen with ``AI_QA_PROVIDER`` and defaults to OpenAI (a different
vendor from the Anthropic generator).
"""

from __future__ import annotations

import logging

from app.ai.features import AiFeature
from app.ai.parsers import parse_json_object
from app.ai.usage import AiUsageContext
from app.core.config import get_settings
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.openai.client import OpenAIClient
from app.prompts.loader import load_prompt, render
from app.schemas.ai import QAVerdict

logger = logging.getLogger("app.ai.qa")

_VALID_STATUSES = {"ok", "concerns"}
_MAX_NOTES = 5


def make_qa_client(provider: str, context: AiUsageContext | None = None):
    """Return a QA provider client exposing ``async complete`` + ``is_configured``.

    Defaults to OpenAI (a different vendor from the Anthropic generator). Anthropic
    is allowed too — useful for testing the wiring against a single available key.
    """
    if provider == "anthropic":
        return AnthropicClient(context)
    return OpenAIClient(context)


class QAReviewer:
    feature = AiFeature.QA_REVIEW

    def __init__(self, qa_client=None, settings=None) -> None:
        self._settings = settings or get_settings().qa
        self._provider = self._settings.provider
        self._client = qa_client or make_qa_client(self._provider)

    @property
    def is_enabled(self) -> bool:
        """QA runs only when explicitly enabled AND the provider is configured."""
        return bool(self._settings.enabled) and self._client.is_configured

    async def review(
        self,
        *,
        content: str,
        content_label: str,
        preamble: str,
        facts: str,
        usage: AiUsageContext | None = None,
    ) -> QAVerdict:
        """Independently review ``content``; degrade to ``not_reviewed`` cleanly."""
        if not self.is_enabled or not (content or "").strip():
            return QAVerdict(status="not_reviewed")

        model = getattr(self._client, "_settings", None)
        model_name = getattr(model, "model", None)
        try:
            system = load_prompt("qa/system.txt")
            prompt = render(
                load_prompt("qa/user_template.txt"),
                {
                    "preamble": preamble or "(no client rules on file)",
                    "facts": facts or "(no facts provided)",
                    "content_label": content_label,
                    "content": content,
                },
            )
            raw = await self._client.complete(
                system=system, prompt=prompt, max_tokens=1000, context=usage
            )
        except Exception:
            logger.warning("QA review failed (provider=%s)", self._provider, exc_info=True)
            return QAVerdict(status="not_reviewed", provider=self._provider, model=model_name)

        payload = parse_json_object(raw)
        if payload is None:
            return QAVerdict(status="not_reviewed", provider=self._provider, model=model_name)

        status = str(payload.get("status") or "").lower()
        if status not in _VALID_STATUSES:
            status = "ok"
        notes = [
            str(n).strip() for n in (payload.get("notes") or []) if isinstance(n, str) and n.strip()
        ][:_MAX_NOTES]
        summary = payload.get("summary")
        return QAVerdict(
            status=status,  # type: ignore[arg-type]
            provider=self._provider,
            model=model_name,
            notes=notes,
            summary=str(summary).strip() if isinstance(summary, str) and summary.strip() else None,
        )
