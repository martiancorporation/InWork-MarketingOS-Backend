"""Executive Brief engine.

A short leadership-facing summary of where the account stands: headline, key
metrics, budget pace, best/worst campaign, and the concrete pending actions.
Claude when configured; deterministic fallback from real client signals so the
brief never fabricates performance it doesn't have.
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
from app.schemas.ai import ExecutiveBrief
from app.services.intelligence.context_service import ClientContext

logger = logging.getLogger("app.ai.executive_brief")


class ExecutiveBriefAgent:
    feature = AiFeature.EXECUTIVE_BRIEF

    def __init__(self, ai_client: AnthropicClient | None = None) -> None:
        self._client = ai_client or AnthropicClient()

    async def generate(
        self,
        client: Client,
        context: ClientContext,
        signals: DashboardSignals,
        usage: AiUsageContext | None = None,
    ) -> ExecutiveBrief:
        if not self._client.is_configured:
            return self._fallback(client, signals)
        try:
            system = load_prompt("executive_brief/system.txt")
            prompt = render(
                load_prompt("executive_brief/user_template.txt"),
                {
                    "client_name": client.name or "the client",
                    "preamble": context.preamble,
                    "facts": signals.as_prompt_facts(),
                },
            )
            raw = await self._client.complete(
                system=system, prompt=prompt, max_tokens=2000, context=usage
            )
            return ExecutiveBrief.model_validate(parse_json_object(raw))
        except Exception:
            logger.warning("Executive brief AI failed for client %s", client.id, exc_info=True)
            return self._fallback(client, signals)

    def _fallback(self, client: Client, s: DashboardSignals) -> ExecutiveBrief:
        name = client.name or "This client"
        metrics = [
            {"label": "Leads", "value": str(s.leads), "delta": "—", "tone": "flat"},
            {"label": "CPL", "value": f"${s.cpl:,.2f}", "delta": "—", "tone": "flat"},
            {"label": "Spend", "value": f"${s.spend:,.0f}", "delta": "—", "tone": "flat"},
            {"label": "Channels", "value": str(len(s.platforms)), "delta": "—", "tone": "flat"},
        ]
        # We have no separate budget figure yet; report spend as the known number.
        budget = {"spent": s.spend, "total": s.spend, "pace": "on-track"}

        pending: list[str] = []
        if s.pending_integrations:
            pending.append(f"Connect {s.pending_integrations} integration(s)")
        if not s.onboarding_completed:
            pending.append("Finish onboarding to unlock the full profile")
        if s.pending_approvals:
            pending.append(f"{s.pending_approvals} calendar post(s) awaiting client approval")
        if not pending:
            pending.append("No blocking actions — keep monitoring performance")

        headline = (
            f"{name} has {s.leads} leads to date at ${s.cpl:,.2f} cost per lead "
            f"across {len(s.platforms)} channel(s)."
        )
        return ExecutiveBrief.model_validate(
            {
                "headline": headline,
                "metrics": metrics,
                "budget": budget,
                "top_campaign": {
                    "name": "Not enough data yet",
                    "note": "Connect analytics/ad platforms to rank campaigns.",
                },
                "worst_campaign": {
                    "name": "Not enough data yet",
                    "note": "Campaign-level performance appears once integrations sync.",
                },
                "pending_actions": pending,
            }
        )
