"""Health Score engine.

Produces a 0-100 account-health score, a band, and the signed drivers behind it.
Uses Claude when configured (grounded in the client's directive preamble + real
signals); otherwise falls back to a deterministic score computed from those same
signals, so the dashboard always renders.
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
from app.schemas.ai import HealthScore
from app.services.intelligence.context_service import ClientContext

logger = logging.getLogger("app.ai.health_score")


def _band(score: int) -> str:
    if score >= 85:
        return "excellent"
    if score >= 70:
        return "good"
    if score >= 55:
        return "attention"
    return "critical"


class HealthScoreAgent:
    feature = AiFeature.HEALTH_SCORE

    def __init__(self, ai_client: AnthropicClient | None = None) -> None:
        self._client = ai_client or AnthropicClient()

    async def generate(
        self,
        client: Client,
        context: ClientContext,
        signals: DashboardSignals,
        usage: AiUsageContext | None = None,
    ) -> HealthScore:
        if not self._client.is_configured:
            return self._fallback(signals)
        try:
            system = load_prompt("health_score/system.txt")
            prompt = render(
                load_prompt("health_score/user_template.txt"),
                {
                    "client_name": client.name or "the client",
                    "preamble": context.preamble,
                    "facts": signals.as_prompt_facts(),
                },
            )
            raw = await self._client.complete(
                system=system, prompt=prompt, max_tokens=1500, context=usage
            )
            payload = parse_json_object(raw)
            result = HealthScore.model_validate(payload)
            # Keep the band consistent with the score regardless of the model.
            result.band = _band(result.score)  # type: ignore[assignment]
            return result
        except Exception:
            logger.warning("Health score AI failed for client %s", client.id, exc_info=True)
            return self._fallback(signals)

    def _fallback(self, s: DashboardSignals) -> HealthScore:
        """Deterministic score from real setup + performance signals."""
        drivers: list[dict] = []
        score = 50

        conn = min(20, s.connected_integrations * 5)
        if conn:
            score += conn
            drivers.append({"label": f"{s.connected_integrations} integrations connected", "delta": conn})
        if s.pending_integrations:
            pen = min(15, s.pending_integrations * 5)
            score -= pen
            drivers.append({"label": f"{s.pending_integrations} integrations not connected", "delta": -pen})
        if s.onboarding_completed:
            score += 8
            drivers.append({"label": "Onboarding complete", "delta": 8})
        else:
            score -= 8
            drivers.append({"label": "Onboarding incomplete", "delta": -8})
        if s.has_profile:
            score += 6
            drivers.append({"label": "Intelligence profile ready", "delta": 6})
        if s.brand_voice:
            score += 3
        if s.goals:
            score += 3
        if s.pending_approvals:
            pen = min(10, s.pending_approvals * 2)
            score -= pen
            drivers.append({"label": f"{s.pending_approvals} posts awaiting approval", "delta": -pen})

        score = max(20, min(98, score))
        return HealthScore.model_validate(
            {"score": score, "band": _band(score), "drivers": drivers[:5]}
        )
