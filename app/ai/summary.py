"""Client Summary Agent.

Deeply reads the full onboarding corpus (fields + every file's contents) and
produces a structured, comprehensive client summary: who they are, what they
want / don't want, goals, expectations, design & content preferences, and
restrictions.

Uses Claude when configured; otherwise falls back to a deterministic summary
assembled from the structured client fields, so the pipeline always yields a
usable profile. File content is presented as untrusted data, never instructions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.ai.parsers import parse_json_object
from app.ai.usage import AiUsageContext
from app.integrations.anthropic.client import AnthropicClient
from app.models.client import Client
from app.prompts.loader import load_prompt, render

logger = logging.getLogger("app.ai.summary")

_SECTIONS = (
    "identity",
    "wants",
    "does_not_want",
    "business_goals",
    "expectations",
    "design_preferences",
    "content_requirements",
    "restrictions",
)


@dataclass
class SummaryResult:
    profile: dict[str, Any]
    summary_md: str
    model: str | None
    ai_generated: bool


class SummaryAgent:
    def __init__(self, client: AnthropicClient | None = None) -> None:
        self._client = client or AnthropicClient()

    async def summarize(
        self, client: Client, corpus: str, context: AiUsageContext | None = None
    ) -> SummaryResult:
        if not self._client.is_configured or not corpus.strip():
            return self._fallback(client, corpus)
        try:
            system = load_prompt("client_summary/system.txt")
            prompt = render(
                load_prompt("client_summary/user_template.txt"),
                {"client_name": client.name or "the client", "corpus": corpus},
            )
            raw = await self._client.complete(
                system=system, prompt=prompt, max_tokens=8000, context=context
            )
        except Exception:
            logger.warning("Summary agent failed for client %s", client.id, exc_info=True)
            return self._fallback(client, corpus)

        payload = parse_json_object(raw)
        if payload is None:
            return self._fallback(client, corpus)
        profile = {k: payload.get(k) for k in _SECTIONS}
        return SummaryResult(
            profile=profile,
            summary_md=str(payload.get("summary_md") or "").strip() or _render_md(client, profile),
            model=self._client._settings.model,  # noqa: SLF001 - model id only
            ai_generated=True,
        )

    def _fallback(self, client: Client, corpus: str) -> SummaryResult:
        """Structured summary from the client's own fields — no model call."""
        goals = (client.goals or "").strip()
        restrictions = [
            e.text for e in client.compliance_entries
            if getattr(e.kind, "value", e.kind) in {"banned", "rule", "required"}
        ]
        profile: dict[str, Any] = {
            "identity": _join(
                client.name, client.business_type, client.industry, client.location
            ),
            "wants": goals or None,
            "does_not_want": _join(*[e.text for e in client.compliance_entries
                                     if getattr(e.kind, "value", e.kind) == "banned"]) or None,
            "business_goals": [goals] if goals else [],
            "expectations": None,
            "design_preferences": {
                "brand_voice": client.brand_voice,
                "colors": [c.hex for c in client.brand_colors],
                "fonts": [f.family for f in client.brand_fonts],
                "guidelines": client.color_guidelines,
            },
            "content_requirements": client.brand_voice,
            "restrictions": restrictions,
        }
        return SummaryResult(
            profile=profile, summary_md=_render_md(client, profile),
            model=None, ai_generated=False,
        )


def _join(*parts: str | None) -> str:
    return " · ".join(p for p in parts if (p or "").strip())


def _render_md(client: Client, profile: dict[str, Any]) -> str:
    lines = [f"# Client summary — {client.name or 'Client'}", ""]
    labels = {
        "identity": "Who they are",
        "wants": "What they want",
        "does_not_want": "What they do not want",
        "business_goals": "Business goals",
        "expectations": "Expectations",
        "design_preferences": "Design preferences",
        "content_requirements": "Content requirements",
        "restrictions": "Restrictions & special instructions",
    }
    for key, label in labels.items():
        value = profile.get(key)
        if not value:
            continue
        lines.append(f"## {label}")
        if isinstance(value, list):
            lines += [f"- {v}" for v in value]
        elif isinstance(value, dict):
            lines += [f"- **{k}**: {v}" for k, v in value.items() if v]
        else:
            lines.append(str(value))
        lines.append("")
    return "\n".join(lines).strip()
