"""Unit tests: AI cost pricing + the OpenRouterClient usage instrumentation."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.ai.pricing import UsageBreakdown, price
from app.ai.usage import AiUsageContext

# ---- pricing ----


def test_price_known_model():
    cost = price(
        "anthropic/claude-opus-5",
        UsageBreakdown(input_tokens=1_000_000, output_tokens=1_000_000),
    )
    assert cost.priced is True
    assert cost.input_cost == Decimal("5.000000")
    assert cost.output_cost == Decimal("25.000000")
    assert cost.total_cost == Decimal("30.000000")


def test_price_includes_cache_tokens():
    cost = price(
        "anthropic/claude-opus-5",
        UsageBreakdown(cache_write_tokens=1_000_000, cache_read_tokens=1_000_000),
    )
    # 6.25 (write) + 0.50 (read)
    assert cost.cache_cost == Decimal("6.750000")
    assert cost.total_cost == Decimal("6.750000")


def test_price_verified_low_cost_models():
    """Live-verified against OpenRouter's /models catalog (Sept 2026) — the
    models Ayon Das recommended as cheaper alternatives to Claude."""
    cases = [
        ("qwen/qwen3.7-flash", "0.03", "0.13"),
        ("deepseek/deepseek-v4.1-flash", "0.15", "0.60"),
        ("z-ai/glm-5.3-flash", "0.15", "0.50"),
        ("minimax/minimax-m3", "0.30", "1.20"),
        ("openai/gpt-5.6-luna", "0.20", "1.20"),
    ]
    for model, input_rate, output_rate in cases:
        cost = price(model, UsageBreakdown(input_tokens=1_000_000, output_tokens=1_000_000))
        assert cost.priced is True, model
        assert cost.input_cost == Decimal(input_rate).quantize(Decimal("0.000001")), model
        assert cost.output_cost == Decimal(output_rate).quantize(Decimal("0.000001")), model
        # Every one of these is meaningfully cheaper than Sonnet 5 ($2/$10).
        assert cost.total_cost < Decimal("12.000000"), model


def test_broken_cheap_model_id_has_no_pricing_entry():
    # Regression guard for the root-cause bug: the previously-configured
    # OPENROUTER_CHEAP_MODEL id was never a real OpenRouter model.
    cost = price(
        "anthropic/claude-haiku-4-5-20251001",
        UsageBreakdown(input_tokens=1000, output_tokens=1000),
    )
    assert cost.priced is False


def test_price_unknown_model_is_zero_and_flagged():
    cost = price("some-unlisted-model", UsageBreakdown(input_tokens=1000, output_tokens=1000))
    assert cost.priced is False
    assert cost.total_cost == Decimal("0")


def test_usage_breakdown_total():
    u = UsageBreakdown(input_tokens=10, output_tokens=5, cache_write_tokens=2, cache_read_tokens=3)
    assert u.total_tokens == 20


# ---- instrumentation: every call records usage via record_usage ----


def _fake_body(request_id: str = "gen_test_1") -> dict:
    return {
        "id": request_id,
        "choices": [{"message": {"content": "hello world"}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40},
    }


def _make_client(monkeypatch, capture, raise_exc=None):
    from app.integrations.llm import openrouter as client_mod

    monkeypatch.setattr(client_mod.OpenRouterClient, "is_configured", property(lambda self: True))

    async def fake_post(self, payload):
        if raise_exc:
            raise raise_exc
        return _fake_body()

    monkeypatch.setattr(client_mod.OpenRouterClient, "_post", fake_post)
    monkeypatch.setattr(client_mod, "record_usage", lambda **kw: capture.append(kw))
    return client_mod.OpenRouterClient()


def test_complete_records_usage(monkeypatch):
    captured: list[dict] = []
    c = _make_client(monkeypatch, captured)
    ctx = AiUsageContext(feature="test.feature")
    out = asyncio.run(c.complete(system="s", prompt="p", context=ctx))

    assert out == "hello world"
    assert len(captured) == 1
    ev = captured[0]
    assert ev["operation"] == "complete"
    assert ev["status"] == "success"
    assert ev["provider"] == "openrouter"
    assert ev["usage"].input_tokens == 120
    assert ev["usage"].output_tokens == 40
    assert ev["request_id"] == "gen_test_1"
    assert ev["context"] is ctx


def test_failed_call_records_error_event(monkeypatch):
    from app.core.exceptions import ServiceUnavailableError

    captured: list[dict] = []
    c = _make_client(monkeypatch, captured, raise_exc=RuntimeError("boom"))
    # The raw httpx exception is translated to a typed error rather than
    # leaking past the client — see OpenRouterClient._invoke.
    with pytest.raises(ServiceUnavailableError, match="boom"):
        asyncio.run(
            c.complete(system="s", prompt="p", context=AiUsageContext(feature="test.feature"))
        )
    assert len(captured) == 1
    assert captured[0]["status"] == "error"
    assert "boom" in captured[0]["error"]
    assert captured[0]["usage"] is None
