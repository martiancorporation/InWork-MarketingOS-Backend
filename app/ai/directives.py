"""Client Intelligence / Directives Agent.

Turns the client corpus + summary into atomic, prioritized **directives** — the
enforceable rules injected into every downstream agent. Each directive has a
type (must / must_not / prefer / avoid / constraint), a category, a priority
tier, a confidence, and optional capability flags.

A deterministic **capability net** always runs (both AI and fallback paths): it
scans for explicit prohibitions like "no AI-generated text" and pins them as
mandatory ``must_not`` directives with machine-readable flags — so safety rules
never depend on the model noticing them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.ai.parsers import parse_json_object
from app.ai.usage import AiUsageContext
from app.integrations.anthropic.client import AnthropicClient
from app.models.client import Client
from app.models.enums import DirectiveTier, DirectiveType
from app.prompts.loader import load_prompt, render

logger = logging.getLogger("app.ai.directives")

# type -> (tier, rank). Lower rank = higher priority.
_TIER_BY_TYPE = {
    DirectiveType.must_not.value: (DirectiveTier.mandatory.value, 0),
    DirectiveType.must.value: (DirectiveTier.required.value, 10),
    DirectiveType.constraint.value: (DirectiveTier.required.value, 20),
    DirectiveType.avoid.value: (DirectiveTier.preference.value, 30),
    DirectiveType.prefer.value: (DirectiveTier.preference.value, 40),
}

# Deterministic capability net: (pattern) -> (flag, value, text, category).
_CAPABILITY_RULES: list[tuple[re.Pattern, str, Any, str, str]] = [
    (re.compile(
        r"(?:no|not|never|without|don'?t|do\s*n[o']?t|do not|avoid)\b[^.\n]{0,30}"
        r"ai[- ]?generated|no ai\b[^.\n]{0,20}(?:text|content|copy)|human[- ]written only",
        re.I),
     "ai_text_generation", False, "Never use AI-generated text in deliverables", "content"),
    (re.compile(r"no stock (?:photos|images|imagery)", re.I),
     "stock_imagery", False, "Do not use stock photos or imagery", "design"),
    (re.compile(r"no emoji|without emoji", re.I),
     "emoji", False, "Do not use emoji", "content"),
]


@dataclass
class Directive:
    type: str
    category: str
    text: str
    tier: str
    rank: int
    confidence: float = 1.0
    capability_flags: dict[str, Any] = field(default_factory=dict)
    source_key: str | None = None


class DirectivesAgent:
    def __init__(self, client: AnthropicClient | None = None) -> None:
        self._client = client or AnthropicClient()

    async def extract(
        self,
        client: Client,
        corpus: str,
        summary: dict | None = None,
        context: AiUsageContext | None = None,
    ) -> list[Directive]:
        directives = (
            await self._extract_ai(client, corpus, summary, context)
            if (self._client.is_configured and corpus.strip())
            else self._fallback(client)
        )
        # The capability net runs regardless — deterministic safety rules always win.
        directives += self._capability_net(corpus, client)
        return directives

    async def _extract_ai(
        self, client: Client, corpus: str, summary: dict | None, context
    ) -> list[Directive]:
        try:
            system = load_prompt("client_intelligence/system.txt")
            prompt = render(
                load_prompt("client_intelligence/user_template.txt"),
                {"client_name": client.name or "the client", "corpus": corpus},
            )
            raw = await self._client.complete(
                system=system, prompt=prompt, max_tokens=2048, context=context
            )
        except Exception:
            logger.warning("Directives agent failed for %s", client.id, exc_info=True)
            return self._fallback(client)

        payload = parse_json_object(raw)
        items = (payload or {}).get("directives")
        if not isinstance(items, list):
            return self._fallback(client)
        out: list[Directive] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            d = self._normalize(
                dtype=str(item.get("type", "prefer")),
                category=str(item.get("category", "general")),
                text=str(item.get("text", "")).strip(),
                confidence=_as_float(item.get("confidence"), 0.8),
            )
            if d:
                out.append(d)
        return out or self._fallback(client)

    def _fallback(self, client: Client) -> list[Directive]:
        """Directives derived deterministically from structured client fields."""
        out: list[Directive] = []
        kind_map = {
            "banned": (DirectiveType.must_not.value, "content"),
            "required": (DirectiveType.must.value, "content"),
            "rule": (DirectiveType.constraint.value, "process"),
            "brand-voice": (DirectiveType.prefer.value, "brand"),
            "note": (DirectiveType.constraint.value, "general"),
        }
        for e in client.compliance_entries:
            kind = getattr(e.kind, "value", e.kind)
            dtype, category = kind_map.get(kind, (DirectiveType.constraint.value, "general"))
            d = self._normalize(dtype, category, (e.text or "").strip(), 0.9, "field:compliance")
            if d:
                out.append(d)
        if (client.brand_voice or "").strip():
            out.append(self._normalize(
                DirectiveType.prefer.value, "brand",
                f"Match the brand voice: {client.brand_voice.strip()}", 0.9, "field:brand",
            ))
        return [d for d in out if d]

    def _capability_net(self, corpus: str, client: Client) -> list[Directive]:
        haystack = "\n".join(
            filter(None, [corpus, client.brand_voice, client.color_guidelines,
                          *[e.text for e in client.compliance_entries]])
        )
        found: list[Directive] = []
        for pattern, flag, value, text, category in _CAPABILITY_RULES:
            if pattern.search(haystack):
                d = self._normalize(DirectiveType.must_not.value, category, text, 1.0)
                d.capability_flags = {flag: value}
                found.append(d)
        return found

    @staticmethod
    def _normalize(
        dtype: str, category: str, text: str, confidence: float, source_key: str | None = None
    ) -> Directive | None:
        if not text:
            return None
        if dtype not in _TIER_BY_TYPE:
            dtype = DirectiveType.prefer.value
        tier, rank = _TIER_BY_TYPE[dtype]
        # Low-confidence model guesses drop to the inferred tier.
        if confidence < 0.5 and tier == DirectiveTier.preference.value:
            tier, rank = DirectiveTier.inferred.value, 50
        return Directive(
            type=dtype, category=category or "general", text=text[:2000],
            tier=tier, rank=rank, confidence=round(confidence, 3), source_key=source_key,
        )


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
