"""Unit tests: OpenRouterClient's usage instrumentation (every call records
usage via ``record_usage``). Pricing itself is covered in
``tests/unit/test_pricing.py``."""

from __future__ import annotations

import asyncio

import pytest

from app.ai.usage import AiUsageContext

_MODEL = "test-vendor/cheap-fast"  # from tests/conftest.py's FAKE_CATALOG


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
    out = asyncio.run(c.complete(system="s", prompt="p", model=_MODEL, context=ctx))

    assert out == "hello world"
    assert len(captured) == 1
    ev = captured[0]
    assert ev["operation"] == "complete"
    assert ev["status"] == "success"
    assert ev["provider"] == "openrouter"
    assert ev["model"] == _MODEL
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
            c.complete(
                system="s", prompt="p", model=_MODEL, context=AiUsageContext(feature="test.feature")
            )
        )
    assert len(captured) == 1
    assert captured[0]["status"] == "error"
    assert "boom" in captured[0]["error"]
    assert captured[0]["usage"] is None


def test_no_model_configured_raises_before_ever_calling_the_provider(monkeypatch):
    """No fallback model exists anywhere in code/settings anymore — a call
    with no resolvable model must fail clearly, the same way an unconfigured
    provider does, rather than silently hitting the API with a blank model."""
    from app.core.exceptions import ServiceUnavailableError

    captured: list[dict] = []
    c = _make_client(monkeypatch, captured)
    with pytest.raises(ServiceUnavailableError, match="No AI model is configured"):
        asyncio.run(c.complete(system="s", prompt="p", model=None))
    assert captured == []  # never even reached _post/record_usage
