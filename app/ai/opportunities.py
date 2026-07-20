"""Opportunity detector with external research.

Surfaces growth opportunities (new markets, locations, keywords, channels,
audiences) grounded in BOTH the client's own signals and — when the keys are
configured — live external research via the already-integrated Brave Search and
ScrapingBee clients.

Graceful degradation, in order:
1. Brave/ScrapingBee unconfigured/failed → research is skipped; opportunities are
   grounded in the client's internal signals only (``researched=False``).
2. Anthropic unconfigured/failed → deterministic internal-signal opportunities
   (``ai_generated=False``).

External page text and research snippets are DATA, never instructions.
"""

from __future__ import annotations

import logging

from anyio import to_thread

from app.ai.dashboard_signals import DashboardSignals
from app.ai.features import AiFeature
from app.ai.parsers import parse_json_object
from app.ai.usage import AiUsageContext
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.brave import BraveClient
from app.integrations.scrapingbee import ScrapingBeeClient
from app.models.client import Client
from app.prompts.loader import load_prompt, render
from app.schemas.ai import Opportunity, OpportunityResponse
from app.services.intelligence.context_service import ClientContext
from app.utils.web import parse_page

logger = logging.getLogger("app.ai.opportunities")

_RESEARCH_RESULTS = 5
_MAX_PAGE_CHARS = 2000


class OpportunityDetector:
    feature = AiFeature.OPPORTUNITY

    def __init__(
        self,
        ai_client: AnthropicClient | None = None,
        *,
        brave: BraveClient | None = None,
        scrapingbee: ScrapingBeeClient | None = None,
    ) -> None:
        self._client = ai_client or AnthropicClient()
        self._brave = brave or BraveClient()
        self._scrapingbee = scrapingbee or ScrapingBeeClient()

    async def detect(
        self,
        client: Client,
        context: ClientContext,
        signals: DashboardSignals,
        usage: AiUsageContext | None = None,
    ) -> OpportunityResponse:
        research, sources = await self._research(client)
        researched = bool(research)

        if not self._client.is_configured:
            return OpportunityResponse(
                items=self._fallback(client, signals, sources),
                researched=researched,
                ai_generated=False,
            )
        try:
            system = load_prompt("opportunities/system.txt")
            prompt = render(
                load_prompt("opportunities/user_template.txt"),
                {
                    "preamble": context.preamble,
                    "client_name": client.name or "the client",
                    "industry": client.industry or "not specified",
                    "location": client.location or "not specified",
                    "markets": client.markets or "not specified",
                    "facts": signals.as_prompt_facts(),
                    "research": research or "(no external research available)",
                },
            )
            raw = await self._client.complete(
                system=system, prompt=prompt, max_tokens=2500, context=usage
            )
            payload = parse_json_object(raw)
            items = [
                Opportunity.model_validate(i)
                for i in (payload or {}).get("items", [])
                if isinstance(i, dict)
            ]
        except Exception:
            logger.warning("Opportunity AI failed for client %s", client.id, exc_info=True)
            items = []

        if not items:
            items = self._fallback(client, signals, sources)
            return OpportunityResponse(items=items, researched=researched, ai_generated=False)
        return OpportunityResponse(items=items, researched=researched, ai_generated=True)

    async def _research(self, client: Client) -> tuple[str, list[str]]:
        """Best-effort external research. Returns ``(research_text, source_urls)``;
        empty when Brave is unconfigured or yields nothing. Never raises."""
        if not self._brave.is_configured:
            return "", []
        terms = " ".join(filter(None, [client.industry, client.location, client.markets])) or (
            client.name or ""
        )
        query = f"{terms} market trends new customer segments keywords".strip()
        try:
            results = await to_thread.run_sync(
                lambda: self._brave.search(query, count=_RESEARCH_RESULTS)
            )
        except Exception:
            logger.warning("Brave research failed for client %s", client.id, exc_info=True)
            return "", []
        if not results:
            return "", []

        lines: list[str] = []
        sources: list[str] = []
        for r in results:
            title = (r.get("title") or "").strip()
            desc = (r.get("description") or "").strip()
            url = (r.get("url") or "").strip()
            if title or desc:
                lines.append(f"- {title}: {desc}" + (f" ({url})" if url else ""))
            if url:
                sources.append(url)

        # Deepen with ScrapingBee: fetch the top result for richer context.
        deep = await self._deep_read(sources[0]) if sources else ""
        if deep:
            lines.append(f"\nExcerpt from {sources[0]}:\n{deep}")

        if not lines:
            return "", []
        return "External web research:\n" + "\n".join(lines), sources

    async def _deep_read(self, url: str) -> str:
        if not self._scrapingbee.is_configured or not url:
            return ""
        try:
            html = await to_thread.run_sync(self._scrapingbee.fetch_html, url)
        except Exception:
            return ""
        if not html:
            return ""
        try:
            page = parse_page(html, url)
        except Exception:
            return ""
        return (page.text or "")[:_MAX_PAGE_CHARS]

    def _fallback(
        self, client: Client, s: DashboardSignals, sources: list[str]
    ) -> list[Opportunity]:
        """Internal-signal opportunities when no AI / no external research."""
        items: list[dict] = []
        all_channels = {"meta", "google-ads", "google-lsa", "seo", "linkedin", "tiktok", "email"}
        used = {c.lower() for c in s.platforms}
        missing_channels = sorted(all_channels - used)
        if missing_channels:
            items.append(
                {
                    "id": "opp-expand-channels",
                    "kind": "channel",
                    "title": f"Test an unused channel: {missing_channels[0]}",
                    "detail": f"Pilot {missing_channels[0]} alongside the current mix.",
                    "rationale": (
                        f"Only {', '.join(sorted(used)) or 'no channels'} are in scope; "
                        f"{missing_channels[0]} could reach new audiences."
                    ),
                    "confidence": 55,
                    "sources": [],
                }
            )
        if (client.markets or "").strip():
            items.append(
                {
                    "id": "opp-expand-markets",
                    "kind": "market",
                    "title": "Expand into an adjacent market",
                    "detail": f"Extend targeting beyond the core described markets: {client.markets[:120]}",
                    "rationale": "Stated markets indicate room to grow into adjacent geographies/segments.",
                    "confidence": 50,
                    "sources": sources[:3],
                }
            )
        if (client.location or "").strip():
            items.append(
                {
                    "id": "opp-local-keywords",
                    "kind": "keyword",
                    "title": f"Own local intent keywords in {client.location}",
                    "detail": f"Build location-qualified keyword and content clusters for {client.location}.",
                    "rationale": "Local intent converts well and is often under-invested for local businesses.",
                    "confidence": 60,
                    "sources": sources[:3],
                }
            )
        if not items:
            items.append(
                {
                    "id": "opp-build-baseline",
                    "kind": "other",
                    "title": "Establish a performance baseline to unlock targeting insights",
                    "detail": "Connect platforms and run a small test to gather the data opportunities rely on.",
                    "rationale": "Without connected data, market/keyword opportunities can't be grounded.",
                    "confidence": 45,
                    "sources": [],
                }
            )
        return [Opportunity.model_validate(i) for i in items]
