"""Rate limiting for sensitive routes.

A sliding-window log keyed by ``(scope, client-ip)`` guards brute-force (login)
and cost-abuse (paid AI) endpoints. Used as a FastAPI dependency::

    @router.post("/login", dependencies=[Depends(RateLimit("login", times=10, seconds=60))])

Scope & caveats:
- **Per-process by default** (``RATE_LIMIT_BACKEND=memory``) — state lives in
  memory, so with multiple workers each worker enforces the limit
  independently (an effective limit of ``times * workers``, not ``times``).
  Set ``RATE_LIMIT_BACKEND=redis`` + ``REDIS_URL`` for one limit shared across
  every worker and replica — same interface, same call sites.
- Disabled wholesale when ``settings.app.rate_limit_enabled`` is false (tests).
- Keyed by best-effort client IP: honours a single ``X-Forwarded-For`` hop only
  when the immediate socket peer is a configured trusted proxy
  (``settings.app.trusted_proxy_cidrs``) — otherwise the header is attacker-
  controlled and would let anyone spoof a fresh IP on every request to dodge
  the limit, so it is ignored and the raw socket peer is used instead.
- Each store owns its own clock, deliberately: the in-memory one uses
  ``time.monotonic()`` (immune to NTP steps), while the shared one must use
  wall-clock time because a monotonic reading is only meaningful within a
  single process — scores written by one host would be nonsense to another.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from functools import lru_cache
from typing import Protocol

from fastapi import Request

from app.core.config import get_settings
from app.core.exceptions import TooManyRequestsError

logger = logging.getLogger(__name__)


class RateLimitStore(Protocol):
    """A sliding-window ``times``-per-``seconds`` counter keyed by
    ``(scope, key)``. ``allow`` returns whether *this* hit is within the
    limit, recording it as a side effect when it is. Implementations source
    their own clock — see the module docstring."""

    def allow(self, scope: str, key: str, *, times: int, seconds: float) -> bool: ...

    def forget(self, scope: str, key: str) -> None:
        """Drop a key's history (e.g. a successful login clearing its budget)."""

    def reset(self) -> None:
        """Clear all counters (used by tests)."""


class MemoryRateLimitStore:
    """Per-process in-memory sliding-window store — the default backend."""

    # Buckets are pruned by AGE, not emptiness. A key seen once and never
    # again keeps a non-empty deque forever (nothing revisits it to expire its
    # entries), so pruning "empty buckets" would free nothing at all — the
    # thing to drop is a bucket whose newest hit is long past any window we
    # use. Comfortably above the largest window in the codebase (the 300s
    # account lockout) so pruning can never evict a live bucket.
    _BUCKET_TTL_SECONDS = 3600.0
    _SWEEP_INTERVAL_SECONDS = 300.0
    # Hard ceiling per scope so a flood of attacker-chosen keys (the login
    # limiter is keyed by submitted email) can't grow this unboundedly between
    # sweeps. On overflow the least-recently-used buckets go first.
    _MAX_KEYS_PER_SCOPE = 50_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # scope -> key -> deque[timestamps]. "key" is an IP for the
        # FastAPI-dependency usage below, but `enforce()` accepts any string
        # key (e.g. an email, for an account-level limit that doesn't have an
        # incoming Request to key off of).
        self._hits: dict[str, dict[str, deque[float]]] = defaultdict(lambda: defaultdict(deque))
        self._last_sweep_at = 0.0

    def _prune(self, now: float) -> None:
        """Drop buckets whose newest hit has aged out. Caller holds the lock."""
        if now - self._last_sweep_at < self._SWEEP_INTERVAL_SECONDS:
            return
        self._last_sweep_at = now
        stale_before = now - self._BUCKET_TTL_SECONDS
        for scope, keys in list(self._hits.items()):
            for key, bucket in list(keys.items()):
                if not bucket or bucket[-1] <= stale_before:
                    del keys[key]
            if not keys:
                del self._hits[scope]

    def _enforce_scope_cap(self, scope: str, now: float) -> None:
        """Keep one scope under ``_MAX_KEYS_PER_SCOPE``. Caller holds the lock."""
        keys = self._hits[scope]
        if len(keys) <= self._MAX_KEYS_PER_SCOPE:
            return
        # Oldest-touched first; ties don't matter, this is pressure relief.
        for key, _ in sorted(keys.items(), key=lambda kv: kv[1][-1] if kv[1] else 0.0)[
            : len(keys) - self._MAX_KEYS_PER_SCOPE
        ]:
            del keys[key]
        logger.warning("Rate-limit scope %r hit its key cap; evicted oldest buckets.", scope)

    def allow(self, scope: str, key: str, *, times: int, seconds: float) -> bool:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            bucket = self._hits[scope][key]
            cutoff = now - seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= times:
                return False
            bucket.append(now)
            self._enforce_scope_cap(scope, now)
            return True

    def forget(self, scope: str, key: str) -> None:
        with self._lock:
            self._hits.get(scope, {}).pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._last_sweep_at = 0.0


# Trim the window, count what's left, and add this hit only if it fits — in
# one atomic server-side step. Doing it as separate round-trips lets N
# concurrent requests all read a count below the limit and all be admitted,
# which is exactly the burst a limiter exists to stop.
_REDIS_SLIDING_WINDOW_LUA = """
local key, now, window, limit, member = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
if redis.call('ZCARD', key) >= limit then
  return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window) + 1)
return 1
"""


class RedisRateLimitStore:
    """Shared sliding-window store backed by a Redis sorted set per key —
    gives every gunicorn worker (and every replica) the same, exact count
    instead of each enforcing the limit independently.

    Each hit is a member scored by its wall-clock timestamp (see the module
    docstring on why not monotonic). A TTL on the key means an abandoned
    bucket expires on its own rather than lingering in Redis forever.

    **On a Redis outage this fails OPEN** — a request is allowed and the
    failure is logged at ERROR. That is a deliberate availability choice: the
    limiter is defense-in-depth in front of endpoints that have their own
    protection (bcrypt on login, object-level authz everywhere), so a Redis
    blip should not take authentication down platform-wide. It does mean
    losing Redis temporarily reduces you to no rate limiting, so alert on
    that log line.
    """

    def __init__(self, url: str) -> None:
        import redis

        # Bounded timeouts: a hung Redis must not pin a threadpool worker for
        # the OS-default TCP timeout on every request.
        self._client = redis.Redis.from_url(url, socket_timeout=0.25, socket_connect_timeout=0.25)
        self._script = self._client.register_script(_REDIS_SLIDING_WINDOW_LUA)

    def allow(self, scope: str, key: str, *, times: int, seconds: float) -> bool:
        try:
            allowed = self._script(
                keys=[self._key(scope, key)],
                args=[time.time(), seconds, times, uuid.uuid4().hex],
            )
        except Exception:  # Redis unreachable/erroring — see the class docstring
            logger.error("Rate-limit Redis call failed; allowing request", exc_info=True)
            return True
        return bool(allowed)

    def forget(self, scope: str, key: str) -> None:
        try:
            self._client.delete(self._key(scope, key))
        except Exception:
            logger.error("Rate-limit Redis delete failed", exc_info=True)

    def reset(self) -> None:
        # Only this limiter's keys — never `flushdb`, which would wipe whatever
        # else shares the database.
        try:
            for key in self._client.scan_iter(match="ratelimit:*", count=1000):
                self._client.delete(key)
        except Exception:
            logger.error("Rate-limit Redis reset failed", exc_info=True)

    @staticmethod
    def _key(scope: str, key: str) -> str:
        # redis-py sends length-prefixed bulk strings, so separators in `key`
        # (an email may contain almost anything) can't break out of the key.
        return f"ratelimit:{scope}:{key}"


@lru_cache
def _store() -> RateLimitStore:
    settings = get_settings().app
    if settings.rate_limit_backend == "redis":
        return RedisRateLimitStore(settings.redis_url)
    return MemoryRateLimitStore()


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    # Only honour the header when the direct connection is from a configured
    # trusted proxy — otherwise a client can set any value it likes and get a
    # fresh rate-limit bucket on every request.
    if forwarded and peer != "unknown" and get_settings().app.is_trusted_proxy(peer):
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return peer


def reset() -> None:
    """Clear all counters (used by tests)."""
    _store().reset()


def enforce(scope: str, key: str, *, times: int, seconds: float) -> None:
    """Enforce a ``times``-per-``seconds`` limit for an arbitrary ``(scope,
    key)`` pair — the same sliding-window semantics ``RateLimit`` uses below,
    callable directly for a limit keyed by something other than the request's
    IP (e.g. the submitted login email, to catch a distributed/multi-IP
    brute-force attempt against one account that a per-IP limit alone
    wouldn't)."""
    if not get_settings().app.rate_limit_enabled:
        return
    if not _store().allow(scope, key, times=times, seconds=seconds):
        raise TooManyRequestsError(
            "Too many requests. Please slow down and try again shortly.",
            details={"retry_after_seconds": int(seconds)},
        )


def clear(scope: str, key: str) -> None:
    """Forget a key's history — used to refund an attempt budget once the
    thing it was guarding succeeded (see ``AuthService.login``)."""
    if not get_settings().app.rate_limit_enabled:
        return
    _store().forget(scope, key)


class RateLimit:
    """FastAPI dependency enforcing ``times`` requests per ``seconds`` per IP."""

    def __init__(self, scope: str, *, times: int, seconds: float = 60.0) -> None:
        self.scope = scope
        self.times = times
        self.seconds = seconds

    def __call__(self, request: Request) -> None:
        if not get_settings().app.rate_limit_enabled:
            return
        enforce(self.scope, _client_ip(request), times=self.times, seconds=self.seconds)
