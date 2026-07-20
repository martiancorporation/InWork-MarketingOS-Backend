"""Recommendations engine.

Prioritized, actionable recommendations with a rationale, confidence, and
expected impact. Each carries a stable ``id`` (rec_key) so a human accept/modify/
reject decision can be recorded against it (``recommendation_actions``). Claude
when configured; deterministic fallback grounded in the client's real signals.
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
from app.schemas.ai import Recommendation
from app.services.intelligence.context_service import ClientContext

logger = logging.getLogger("app.ai.recommendations")


class RecommendationsAgent:
    feature = AiFeature.RECOMMENDATION

    def __init__(self, ai_client: AnthropicClient | None = None) -> None:
        self._client = ai_client or AnthropicClient()

    async def generate(
        self,
        client: Client,
        context: ClientContext,
        signals: DashboardSignals,
        usage: AiUsageContext | None = None,
    ) -> list[Recommendation]:
        if not self._client.is_configured:
            return self._fallback(signals)
        try:
            system = load_prompt("recommendations/system.txt")
            prompt = render(
                load_prompt("recommendations/user_template.txt"),
                {
                    "client_name": client.name or "the client",
                    "preamble": context.preamble,
                    "facts": signals.as_prompt_facts(),
                },
            )
            raw = await self._client.complete(
                system=system, prompt=prompt, max_tokens=3000, context=usage
            )
            payload = parse_json_object(raw)
            items = (payload or {}).get("recommendations", [])
            parsed = [Recommendation.model_validate(r) for r in items]
            return parsed or self._fallback(signals)
        except Exception:
            logger.warning("Recommendations AI failed for client %s", client.id, exc_info=True)
            return self._fallback(signals)

    def _fallback(self, s: DashboardSignals) -> list[Recommendation]:
        recs: list[dict] = []
        if s.pending_integrations:
            recs.append(
                {
                    "id": "rec-connect-integrations",
                    "title": "Connect your remaining ad & analytics integrations",
                    "category": "growth",
                    "severity": "high",
                    "summary": f"{s.pending_integrations} platform(s) are not connected, so performance and attribution are blind spots.",
                    "reason": "Without connected platforms the dashboard can't measure spend, leads, or ROI accurately.",
                    "confidence": 90,
                    "expected_impact": "Full-funnel visibility + accurate CPL/ROI",
                    "projection": {
                        "metric": "attribution coverage",
                        "direction": "up",
                        "estimate": "measurable CPL/ROI where it's currently a blind spot",
                        "basis": f"{s.pending_integrations} platform(s) still unconnected",
                    },
                }
            )
        if s.banned_words or s.required_phrases or s.rules:
            n = len(s.banned_words) + len(s.required_phrases) + len(s.rules)
            recs.append(
                {
                    "id": "rec-enforce-brand-rules",
                    "title": "Enforce brand rules across all generated copy",
                    "category": "compliance",
                    "severity": "medium",
                    "summary": f"{n} brand rule(s) are on file — apply them to every ad, caption, and email.",
                    "reason": "Consistent compliance protects the brand and avoids client rejections in review.",
                    "confidence": 85,
                    "expected_impact": "Fewer approval rejections, on-brand output",
                }
            )
        if s.leads == 0 and s.spend == 0:
            recs.append(
                {
                    "id": "rec-launch-first-campaign",
                    "title": "Launch the first campaign to start generating data",
                    "category": "budget",
                    "severity": "medium",
                    "summary": "No spend or leads recorded yet — a small initial test will start the learning loop.",
                    "reason": "Early performance data is required before optimization recommendations become meaningful.",
                    "confidence": 70,
                    "expected_impact": "Baseline CPL + first leads",
                    "projection": {
                        "metric": "leads",
                        "direction": "up",
                        "estimate": "first baseline leads to start the learning loop",
                        "basis": "no spend or leads recorded yet",
                    },
                }
            )
        recs.append(
            {
                "id": "rec-refresh-creative",
                "title": "Plan a creative refresh cadence",
                "category": "creative",
                "severity": "low",
                "summary": "Rotate fresh hooks and hero assets regularly to avoid audience fatigue.",
                "reason": "Creative fatigue is the most common cause of rising CPL on paid social.",
                "confidence": 65,
                "expected_impact": "Sustained CTR, lower CPL drift",
                "projection": {
                    "metric": "CTR",
                    "direction": "up",
                    "estimate": "sustained CTR, slower CPL drift",
                    "basis": "creative fatigue is the most common cause of rising CPL",
                },
            }
        )
        return [Recommendation.model_validate(r) for r in recs]
