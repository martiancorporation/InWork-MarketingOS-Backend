"""Unit tests: the dashboard AI engines' Claude path + fallback, with a fake client."""

from __future__ import annotations

import asyncio

from app.ai.dashboard_signals import DashboardSignals
from app.ai.health_score import HealthScoreAgent
from app.ai.recommendations import RecommendationsAgent
from app.models.client import Client
from app.services.intelligence.context_service import ClientContext


class FakeAI:
    """Stand-in AnthropicClient: reports configured and returns canned text."""

    def __init__(self, raw: str) -> None:
        self._raw = raw

    @property
    def is_configured(self) -> bool:
        return True

    async def complete(self, *, system, prompt, max_tokens=None, context=None) -> str:
        return self._raw


CTX = ClientContext(version=1, preamble="Client rules: keep it on-brand.")
CLIENT = Client(name="Acme Co.")


def test_health_score_ai_path_parses_and_normalizes_band():
    ai = FakeAI('{"score": 82, "band": "critical", "drivers": [{"label": "CTR up", "delta": 5}]}')
    result = asyncio.run(
        HealthScoreAgent(ai_client=ai).generate(CLIENT, CTX, DashboardSignals())
    )
    assert result.score == 82
    # band is recomputed from the score, overriding whatever the model said
    assert result.band == "good"
    assert result.drivers[0].label == "CTR up"


def test_health_score_falls_back_on_bad_json():
    ai = FakeAI("sorry, I can't do that")
    result = asyncio.run(
        HealthScoreAgent(ai_client=ai).generate(
            CLIENT, CTX, DashboardSignals(onboarding_completed=True, brand_voice="Bold", goals="Grow")
        )
    )
    # deterministic fallback still produces a valid score
    assert 20 <= result.score <= 98
    assert result.band in {"excellent", "good", "attention", "critical"}


def test_recommendations_ai_path_parses_items():
    ai = FakeAI(
        '{"recommendations": [{"id": "rec-x", "title": "Do X", "category": "budget",'
        ' "severity": "high", "summary": "s", "reason": "r", "confidence": 88,'
        ' "expected_impact": "big"}]}'
    )
    recs = asyncio.run(
        RecommendationsAgent(ai_client=ai).generate(CLIENT, CTX, DashboardSignals())
    )
    assert len(recs) == 1
    assert recs[0].id == "rec-x"
    assert recs[0].confidence == 88


def test_recommendations_fallback_on_empty_items():
    ai = FakeAI('{"recommendations": []}')
    recs = asyncio.run(
        RecommendationsAgent(ai_client=ai).generate(
            CLIENT, CTX, DashboardSignals(pending_integrations=2)
        )
    )
    # empty model output → deterministic fallback, which flags the missing integrations
    assert any(r.id == "rec-connect-integrations" for r in recs)
