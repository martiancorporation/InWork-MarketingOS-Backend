"""Lightweight, dependency-free rate limiting for sensitive routes.

A sliding-window log keyed by ``(scope, client-ip)`` guards brute-force (login)
and cost-abuse (paid AI) endpoints. Used as a FastAPI dependency::

    @router.post("/login", dependencies=[Depends(RateLimit("login", times=10, seconds=60))])

Scope & caveats:
- **Per-process** — state lives in memory, so with multiple workers each worker
  enforces the limit independently. For exact global limits put a shared store
  (Redis) behind the same interface. This is a deliberate, documented first line
  of defense, not a distributed limiter.
- Disabled wholesale when ``settings.app.rate_limit_enabled`` is false (tests).
- Keyed by best-effort client IP (honours a single ``X-Forwarded-For`` hop when
  present, since the app is expected to run behind a trusted proxy/ALB).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import get_settings
from app.core.exceptions import TooManyRequestsError

_lock = threading.Lock()
# scope -> ip -> deque[timestamps]
_hits: dict[str, dict[str, deque[float]]] = defaultdict(lambda: defaultdict(deque))


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _allow(scope: str, ip: str, *, times: int, seconds: float, now: float) -> bool:
    with _lock:
        bucket = _hits[scope][ip]
        cutoff = now - seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= times:
            return False
        bucket.append(now)
        return True


def reset() -> None:
    """Clear all counters (used by tests)."""
    with _lock:
        _hits.clear()


class RateLimit:
    """FastAPI dependency enforcing ``times`` requests per ``seconds`` per IP."""

    def __init__(self, scope: str, *, times: int, seconds: float = 60.0) -> None:
        self.scope = scope
        self.times = times
        self.seconds = seconds

    def __call__(self, request: Request) -> None:
        if not get_settings().app.rate_limit_enabled:
            return
        ip = _client_ip(request)
        if not _allow(self.scope, ip, times=self.times, seconds=self.seconds, now=time.monotonic()):
            raise TooManyRequestsError(
                "Too many requests. Please slow down and try again shortly.",
                details={"retry_after_seconds": int(self.seconds)},
            )
