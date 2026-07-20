"""Smart missing-information detection for onboarding.

The fixed readiness checklist catches the obvious gaps (no brand voice, no
integrations). This adds an AI pass that infers client/industry-SPECIFIC missing
information beyond that checklist — e.g. licensing details for a regulated
industry, service areas for a local business, deal size for B2B — each with a
rationale.

Graceful degradation: when Anthropic is unconfigured or the call fails, ``detect``
returns just the fixed-checklist gaps (``ai_generated=False``), so the endpoint is
always useful. All client text is treated as DATA, never as instructions.
"""

from __future__ import annotations

import logging

from app.ai.features import AiFeature
from app.ai.parsers import parse_json_object
from app.ai.usage import AiUsageContext
from app.integrations.anthropic.client import AnthropicClient
from app.models.client import Client
from app.prompts.loader import load_prompt, render
from app.schemas.onboarding import MissingInfoItem, MissingInfoReport

logger = logging.getLogger("app.ai.missing_info")

_MAX_ITEMS = 8


class MissingInfoAgent:
    feature = AiFeature.MISSING_INFO

    def __init__(self, ai_client: AnthropicClient | None = None) -> None:
        self._client = ai_client or AnthropicClient()

    async def detect(
        self,
        client: Client,
        checklist_gaps: list[tuple[str, str]],
        usage: AiUsageContext | None = None,
    ) -> MissingInfoReport:
        """Infer missing info. ``checklist_gaps`` are ``(key, label)`` from the
        fixed readiness checklist — always included; the AI adds specific items."""
        base_items = [
            MissingInfoItem(
                key=key,
                label=label,
                rationale="Part of the standard onboarding checklist and not yet provided.",
                source="checklist",
            )
            for key, label in checklist_gaps
        ]
        if not self._client.is_configured:
            return MissingInfoReport(items=base_items, ai_generated=False)

        try:
            system = load_prompt("missing_info/system.txt")
            prompt = render(
                load_prompt("missing_info/user_template.txt"),
                {
                    "client_name": client.name or "the client",
                    "industry": client.industry or "not specified",
                    "business_type": client.business_type or "not specified",
                    "corpus": _corpus(client),
                    "checklist_gaps": _format_gaps(checklist_gaps),
                },
            )
            raw = await self._client.complete(
                system=system, prompt=prompt, max_tokens=1500, context=usage
            )
        except Exception:
            logger.warning("Missing-info AI failed for client %s", client.id, exc_info=True)
            return MissingInfoReport(items=base_items, ai_generated=False)

        payload = parse_json_object(raw)
        items = (payload or {}).get("items")
        if not isinstance(items, list):
            return MissingInfoReport(items=base_items, ai_generated=False)

        ai_items: list[MissingInfoItem] = []
        seen_keys = {i.key for i in base_items}
        for item in items:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            rationale = str(item.get("rationale") or "").strip()
            key = str(item.get("key") or "").strip() or _slug(label)
            if not label or not rationale or key in seen_keys:
                continue
            seen_keys.add(key)
            ai_items.append(MissingInfoItem(key=key, label=label, rationale=rationale, source="ai"))
            if len(ai_items) >= _MAX_ITEMS:
                break

        # Checklist gaps first (they're hard requirements), then AI-inferred items.
        return MissingInfoReport(items=base_items + ai_items, ai_generated=bool(ai_items))


def _format_gaps(gaps: list[tuple[str, str]]) -> str:
    if not gaps:
        return "(the fixed checklist is fully satisfied)"
    return "\n".join(f"- {label}" for _key, label in gaps)


def _slug(label: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in label.lower()).split())[:60]


def _corpus(client: Client) -> str:
    """Compact, labelled dump of what has been captured about the client."""
    parts: list[str] = []

    def add(label: str, value: str | None) -> None:
        if (value or "").strip():
            parts.append(f"{label}: {value.strip()}")

    add("Website", client.website)
    add("Location", client.location)
    add("Markets", client.markets)
    add("About the brand", client.about_brand)
    add("Brand voice", client.brand_voice)
    add("Goals", client.goals)
    if client.platforms:
        parts.append("Platforms: " + ", ".join(p.channel for p in client.platforms))
    if client.compliance_entries:
        parts.append(
            "Compliance & rules:\n"
            + "\n".join(
                f"- [{getattr(e.kind, 'value', e.kind)}] {e.text}"
                for e in client.compliance_entries
            )
        )
    return "\n".join(parts) or "(only the basics have been captured)"
