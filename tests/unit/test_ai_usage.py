"""Unit tests: AI cost pricing + the AnthropicClient usage instrumentation."""

from __future__ import annotations

import asyncio
import types
from decimal import Decimal

import pytest

from app.ai.pricing import UsageBreakdown, price
from app.ai.usage import AiUsageContext, usage_from_message


# ---- pricing ----

def test_price_known_model():
    cost = price("claude-opus-4-8", UsageBreakdown(input_tokens=1_000_000, output_tokens=1_000_000))
    assert cost.priced is True
    assert cost.input_cost == Decimal("15.000000")
    assert cost.output_cost == Decimal("75.000000")
    assert cost.total_cost == Decimal("90.000000")


def test_price_includes_cache_tokens():
    cost = price("claude-opus-4-8", UsageBreakdown(cache_write_tokens=1_000_000, cache_read_tokens=1_000_000))
    # 18.75 (write) + 1.50 (read)
    assert cost.cache_cost == Decimal("20.250000")
    assert cost.total_cost == Decimal("20.250000")


def test_price_unknown_model_is_zero_and_flagged():
    cost = price("some-unlisted-model", UsageBreakdown(input_tokens=1000, output_tokens=1000))
    assert cost.priced is False
    assert cost.total_cost == Decimal("0")


def test_usage_breakdown_total():
    u = UsageBreakdown(input_tokens=10, output_tokens=5, cache_write_tokens=2, cache_read_tokens=3)
    assert u.total_tokens == 20


def test_usage_from_message_reads_all_fields():
    usage = types.SimpleNamespace(
        input_tokens=100, output_tokens=50,
        cache_creation_input_tokens=7, cache_read_input_tokens=3,
    )
    msg = types.SimpleNamespace(usage=usage)
    b = usage_from_message(msg)
    assert (b.input_tokens, b.output_tokens, b.cache_write_tokens, b.cache_read_tokens) == (100, 50, 7, 3)


def test_usage_from_message_handles_missing_usage():
    assert usage_from_message(types.SimpleNamespace()).total_tokens == 0


# ---- instrumentation: every call records usage via record_usage ----

class _FakeUsage:
    input_tokens = 120
    output_tokens = 40
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 10


class _FakeMessage:
    id = "msg_test_1"
    stop_reason = "end_turn"
    usage = _FakeUsage()
    content = [types.SimpleNamespace(type="text", text="hello world")]


class _FakeMessages:
    def __init__(self, raise_exc=None):
        self._raise = raise_exc

    async def create(self, **kwargs):
        if self._raise:
            raise self._raise
        return _FakeMessage()


class _FakeAnthropic:
    def __init__(self, raise_exc=None):
        self.messages = _FakeMessages(raise_exc)


def _make_client(monkeypatch, capture, raise_exc=None):
    from app.integrations.anthropic import client as client_mod

    monkeypatch.setattr(client_mod.AnthropicClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        client_mod.AnthropicClient, "_new_client", lambda self: _FakeAnthropic(raise_exc)
    )
    monkeypatch.setattr(client_mod, "record_usage", lambda **kw: capture.append(kw))
    return client_mod.AnthropicClient()


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
    assert ev["provider"] == "anthropic"
    assert ev["usage"].input_tokens == 120
    assert ev["usage"].output_tokens == 40
    assert ev["usage"].cache_read_tokens == 10
    assert ev["request_id"] == "msg_test_1"
    assert ev["context"] is ctx


def test_failed_call_records_error_event(monkeypatch):
    captured: list[dict] = []
    c = _make_client(monkeypatch, captured, raise_exc=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        asyncio.run(c.complete(system="s", prompt="p", context=AiUsageContext(feature="test.feature")))
    assert len(captured) == 1
    assert captured[0]["status"] == "error"
    assert "boom" in captured[0]["error"]
    assert captured[0]["usage"] is None
