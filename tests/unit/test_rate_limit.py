"""Unit tests for the in-process sliding-window rate limiter."""

from __future__ import annotations

import time

import pytest

from app.core.exceptions import TooManyRequestsError
from app.core.rate_limit import MemoryRateLimitStore, RateLimit, reset


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    """Minimal stand-in exposing the attributes RateLimit reads."""

    def __init__(self, ip: str) -> None:
        self.client = _FakeClient(ip)
        self.headers: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _clear_counters():
    reset()
    yield
    reset()


def test_allows_up_to_limit_then_blocks(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()  # pick up the env override
    limiter = RateLimit("login", times=3, seconds=60)
    req = _FakeRequest("1.2.3.4")

    for _ in range(3):
        limiter(req)  # first three succeed

    with pytest.raises(TooManyRequestsError):
        limiter(req)  # fourth trips the limit

    get_settings.cache_clear()


def test_limits_are_per_ip(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    limiter = RateLimit("login", times=1, seconds=60)

    limiter(_FakeRequest("10.0.0.1"))
    limiter(_FakeRequest("10.0.0.2"))  # different IP — its own budget
    with pytest.raises(TooManyRequestsError):
        limiter(_FakeRequest("10.0.0.1"))  # first IP is now over

    get_settings.cache_clear()


def test_disabled_is_a_noop(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    limiter = RateLimit("login", times=1, seconds=60)
    req = _FakeRequest("1.1.1.1")
    for _ in range(100):
        limiter(req)  # never raises when disabled

    get_settings.cache_clear()


# ---- MemoryRateLimitStore internals ----


def test_store_prunes_buckets_by_age_not_emptiness():
    """A key seen once and never again keeps a NON-empty deque forever —
    nothing revisits it to expire its entries. Pruning only "empty" buckets
    would therefore free nothing at all, which is exactly the leak this
    guards."""
    store = MemoryRateLimitStore()
    for i in range(1000):
        store.allow("login-account", f"user{i}@test.com", times=5, seconds=60)
    assert len(store._hits["login-account"]) == 1000

    # Jump past the bucket TTL and force a sweep on the next call.
    store._last_sweep_at = 0.0
    later = time.monotonic() + store._BUCKET_TTL_SECONDS + 1
    store._prune(later)
    assert store._hits.get("login-account", {}) == {}


def test_store_caps_keys_per_scope():
    store = MemoryRateLimitStore()
    store._MAX_KEYS_PER_SCOPE = 50
    for i in range(200):
        store.allow("login-account", f"user{i}@test.com", times=5, seconds=60)
    assert len(store._hits["login-account"]) <= 50


def test_store_forget_refunds_a_key():
    store = MemoryRateLimitStore()
    for _ in range(3):
        assert store.allow("login-account", "a@test.com", times=3, seconds=60)
    assert not store.allow("login-account", "a@test.com", times=3, seconds=60)

    store.forget("login-account", "a@test.com")
    assert store.allow("login-account", "a@test.com", times=3, seconds=60)
