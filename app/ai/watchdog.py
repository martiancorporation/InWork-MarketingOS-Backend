"""Watchdog engine.

Surfaces alerts (things going wrong) and opportunities (things to capitalize on)
for the account. Claude when configured; deterministic fallback derived from real
setup/pipeline signals so there is always an honest watchlist.
"""

from __future__ import annotations

import logging

from app.ai.dashboard_signals import DashboardSignals
from app.ai.features import AiFeature
from app.ai.parsers import parse_json_object
from app.ai.usage import AiUsageContext
from app.integrations.anthropic.client import AnthropicClient
from app.models.client import Client
from app.prompts.loader import load_prompt, render
from app.schemas.ai import WatchdogItem
from app.services.intelligence.context_service import ClientContext

logger = logging.getLogger("app.ai.watchdog")


class WatchdogAgent:
    feature = AiFeature.WATCHDOG

    def __init__(self, ai_client: AnthropicClient | None = None) -> None:
        self._client = ai_client or AnthropicClient()

    async def generate(
        self,
        client: Client,
        context: ClientContext,
        signals: DashboardSignals,
        usage: AiUsageContext | None = None,
    ) -> list[WatchdogItem]:
        if not self._client.is_configured:
            return self._fallback(signals)
        try:
            system = load_prompt("watchdog/system.txt")
            prompt = render(
                load_prompt("watchdog/user_template.txt"),
                {
                    "client_name": client.name or "the client",
                    "preamble": context.preamble,
                    "facts": signals.as_prompt_facts(),
                },
            )
            raw = await self._client.complete(
                system=system, prompt=prompt, max_tokens=2000, context=usage
            )
            payload = parse_json_object(raw)
            items = (payload or {}).get("items", [])
            parsed = [WatchdogItem.model_validate(i) for i in items]
            return parsed or self._fallback(signals)
        except Exception:
            logger.warning("Watchdog AI failed for client %s", client.id, exc_info=True)
            return self._fallback(signals)

    def _fallback(self, s: DashboardSignals) -> list[WatchdogItem]:
        items: list[dict] = []
        if s.pending_integrations:
            items.append(
                {
                    "id": "w-integrations",
                    "kind": "alert",
                    "severity": "high",
                    "title": f"{s.pending_integrations} integration(s) not connected",
                    "detail": "Analytics and attribution will have gaps until every platform is connected.",
                }
            )
        if s.pending_approvals:
            items.append(
                {
                    "id": "w-approvals",
                    "kind": "alert",
                    "severity": "medium",
                    "title": f"{s.pending_approvals} post(s) awaiting client approval",
                    "detail": "Scheduled content is blocked on client sign-off.",
                }
            )
        if not s.onboarding_completed:
            items.append(
                {
                    "id": "w-onboarding",
                    "kind": "alert",
                    "severity": "medium",
                    "title": "Onboarding is not complete",
                    "detail": "Finish the onboarding wizard so the client profile and rules are fully built.",
                }
            )
        if not s.has_profile:
            items.append(
                {
                    "id": "w-profile",
                    "kind": "alert",
                    "severity": "low",
                    "title": "Intelligence profile still building",
                    "detail": "The summary and directive rules are being generated in the background.",
                }
            )
        if s.goals:
            items.append(
                {
                    "id": "w-goals",
                    "kind": "opportunity",
                    "severity": "low",
                    "title": "Align this week's content with stated goals",
                    "detail": f"Client goals on file: {s.goals[:140]}",
                }
            )
        if not items:
            items.append(
                {
                    "id": "w-ok",
                    "kind": "opportunity",
                    "severity": "low",
                    "title": "Account setup looks healthy",
                    "detail": "No blocking issues detected — keep monitoring performance as data accrues.",
                }
            )
        return [WatchdogItem.model_validate(i) for i in items]
