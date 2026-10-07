"""Unit tests: the live OpenRouter model catalog client
(app/integrations/llm/model_catalog.py).

Network access is faked throughout — these tests exercise the parsing,
caching, and graceful-degradation behavior against a controlled ``httpx.get``
stand-in, never the real endpoint (the autouse ``_fake_model_catalog``
fixture in tests/conftest.py replaces ``get_catalog`` for every OTHER test in
the suite; this file bypasses that fixture on purpose to test the real
implementation)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.integrations.llm import model_catalog

# Captured at import time, before the suite-wide autouse fixture in
# tests/conftest.py (``_fake_model_catalog``) has a chance to monkeypatch
# ``model_catalog.get_catalog`` — this file tests the REAL implementation,
# so it restores it for every test here (see ``_use_real_get_catalog`` below).
_REAL_GET_CATALOG = model_catalog.get_catalog


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


@pytest.fixture(autouse=True)
def _use_real_get_catalog(monkeypatch: pytest.MonkeyPatch):
    """The suite-wide autouse fixture in tests/conftest.py replaces
    ``get_catalog`` with a fixed fake for every OTHER test in the suite —
    restore the real implementation here, since this file tests it
    directly."""
    monkeypatch.setattr(model_catalog, "get_catalog", _REAL_GET_CATALOG)
    model_catalog.invalidate_cache()
    yield
    model_catalog.invalidate_cache()


def _real_payload() -> dict:
    return {
        "data": [
            {
                "id": "vendor/real-model",
                "name": "Real Model",
                "context_length": 128_000,
                "pricing": {
                    "prompt": "0.000002",
                    "completion": "0.00001",
                    "input_cache_read": "0.0000002",
                    "input_cache_write": "0.0000025",
                },
            },
            {
                "id": "vendor/auto-router",
                "name": "Auto Router (variable pricing)",
                "context_length": 1_000_000,
                "pricing": {"prompt": "-1", "completion": "-1"},
            },
        ]
    }


def test_fetch_parses_prices_into_per_million_decimals(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_real_payload()))
    catalog = model_catalog.get_catalog(force_refresh=True)

    real = next(m for m in catalog if m.id == "vendor/real-model")
    assert real.input_per_million == Decimal("2")
    assert real.output_per_million == Decimal("10")
    assert real.cache_read_per_million == Decimal("0.2")
    assert real.cache_write_per_million == Decimal("2.5")


def test_negative_pricing_means_no_fixed_rate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_real_payload()))
    catalog = model_catalog.get_catalog(force_refresh=True)

    router = next(m for m in catalog if m.id == "vendor/auto-router")
    assert router.input_per_million is None
    assert router.output_per_million is None


def test_cache_avoids_a_second_fetch_within_the_ttl(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    def _get(*a, **k):
        calls["n"] += 1
        return _FakeResponse(_real_payload())

    monkeypatch.setattr(httpx, "get", _get)
    model_catalog.get_catalog(force_refresh=True)
    model_catalog.get_catalog()
    model_catalog.get_catalog()
    assert calls["n"] == 1


def test_force_refresh_bypasses_the_cache(monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0}

    def _get(*a, **k):
        calls["n"] += 1
        return _FakeResponse(_real_payload())

    monkeypatch.setattr(httpx, "get", _get)
    model_catalog.get_catalog(force_refresh=True)
    model_catalog.get_catalog(force_refresh=True)
    assert calls["n"] == 2


def test_fetch_failure_falls_back_to_last_good_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_real_payload()))
    good = model_catalog.get_catalog(force_refresh=True)
    assert good  # sanity

    def _boom(*a, **k):
        raise httpx.ConnectError("network is down")

    monkeypatch.setattr(httpx, "get", _boom)
    result = model_catalog.get_catalog(force_refresh=True)
    assert result == good


def test_fetch_failure_with_no_prior_cache_returns_empty_list(monkeypatch: pytest.MonkeyPatch):
    def _boom(*a, **k):
        raise httpx.ConnectError("network is down")

    monkeypatch.setattr(httpx, "get", _boom)
    assert model_catalog.get_catalog(force_refresh=True) == []


def test_get_model_looks_up_by_id(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(_real_payload()))
    model_catalog.get_catalog(force_refresh=True)

    assert model_catalog.get_model("vendor/real-model") is not None
    assert model_catalog.get_model("vendor/does-not-exist") is None


def test_cheapest_model_ignores_unpriced_entries(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "data": [
            {
                "id": "vendor/pricey",
                "name": "Pricey",
                "pricing": {"prompt": "0.00001", "completion": "0.00005"},
            },
            {
                "id": "vendor/cheap",
                "name": "Cheap",
                "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
            },
            {
                "id": "vendor/unpriced-router",
                "name": "Router",
                "pricing": {"prompt": "-1", "completion": "-1"},
            },
        ]
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(payload))
    model_catalog.get_catalog(force_refresh=True)

    cheapest = model_catalog.cheapest_model()
    assert cheapest is not None
    assert cheapest.id == "vendor/cheap"


def test_cheapest_model_none_when_catalog_is_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse({"data": []}))
    model_catalog.get_catalog(force_refresh=True)
    assert model_catalog.cheapest_model() is None
