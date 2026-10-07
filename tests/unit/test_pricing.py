"""Unit tests: catalog-backed cost calculation (app/ai/pricing.py).

Rates come from the fake catalog installed by the suite-wide autouse
``_fake_model_catalog`` fixture (tests/conftest.py), not a real network call.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai import pricing
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _clear_pricing_override():
    get_settings().ai.pricing_json = None
    yield
    get_settings().ai.pricing_json = None


def test_get_rate_reads_the_live_catalog():
    rate = pricing.get_rate("test-vendor/cheap-fast")
    assert rate is not None
    assert rate.input == Decimal("0.10")
    assert rate.output == Decimal("0.40")
    assert rate.cache_write == Decimal("0.10")
    assert rate.cache_read == Decimal("0.02")


def test_unknown_model_has_no_rate():
    assert pricing.get_rate("totally/unknown-model") is None
    assert pricing.has_rate("totally/unknown-model") is False


def test_price_unknown_model_is_zero_cost_and_unpriced():
    cost = pricing.price("totally/unknown-model", pricing.UsageBreakdown(input_tokens=1000))
    assert cost.priced is False
    assert cost.total_cost == Decimal(0)


def test_price_computes_input_and_output_cost():
    usage = pricing.UsageBreakdown(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = pricing.price("test-vendor/mid-tier", usage)
    assert cost.priced is True
    assert cost.input_cost == Decimal("1.000000")
    assert cost.output_cost == Decimal("4.000000")
    assert cost.total_cost == Decimal("5.000000")


def test_ai_pricing_json_override_wins_over_the_catalog():
    get_settings().ai.pricing_json = '{"test-vendor/cheap-fast": {"input": 99, "output": 99}}'
    rate = pricing.get_rate("test-vendor/cheap-fast")
    assert rate is not None
    assert rate.input == Decimal("99")


def test_ai_pricing_json_can_price_a_model_the_catalog_doesnt_know():
    get_settings().ai.pricing_json = '{"custom/private-model": {"input": 1, "output": 2}}'
    rate = pricing.get_rate("custom/private-model")
    assert rate is not None
    assert rate.input == Decimal("1")
    assert rate.output == Decimal("2")


def test_usage_breakdown_total():
    u = pricing.UsageBreakdown(
        input_tokens=10, output_tokens=5, cache_write_tokens=2, cache_read_tokens=3
    )
    assert u.total_tokens == 20


def test_invalid_ai_pricing_json_is_ignored_not_fatal():
    get_settings().ai.pricing_json = "{not valid json"
    # Falls through to the catalog instead of raising.
    rate = pricing.get_rate("test-vendor/cheap-fast")
    assert rate is not None
    assert rate.input == Decimal("0.10")
