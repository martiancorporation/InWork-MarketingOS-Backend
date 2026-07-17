"""Per-request correlation id, propagated through logs.

A pure-ASGI middleware assigns each request an id (honouring an inbound
``X-Request-ID`` when present), stashes it in a context variable, and echoes it
on the response. A logging filter injects that id into every log record emitted
while handling the request, so lines from different requests can be told apart
in aggregated logs.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
# Before/after diff a service attaches to the current request so the audit row
# records *what changed* (accountability), not just which endpoint was hit.
#
# The ContextVar holds a **mutable holder** (not the diff directly). A sync route
# runs in a threadpool, whose context is a *copy* of the request's — so rebinding
# a ContextVar inside the route would be lost on the way back to the async audit
# middleware. Mutating a shared holder object is visible in both contexts (it's
# the same object by reference), so the diff survives the threadpool hop.
_audit_changes: ContextVar[dict | None] = ContextVar("audit_changes", default=None)

REQUEST_ID_HEADER = "x-request-id"


def get_request_id() -> str:
    return _request_id.get()


def begin_audit_changes():
    """Seed a fresh per-request holder. Call once at the start of a request (the
    audit middleware does this) and pass the returned token to ``reset``."""
    return _audit_changes.set({"changes": None})


def set_audit_changes(changes: dict | None) -> None:
    """Attach a per-field ``{field: {before, after}}`` diff to this request's audit
    row. Mutates the shared holder so it survives the sync-route threadpool hop; if
    no holder was seeded (e.g. a non-request context), one is created lazily."""
    holder = _audit_changes.get()
    if holder is None:
        _audit_changes.set({"changes": changes})
    else:
        holder["changes"] = changes


def get_audit_changes() -> dict | None:
    holder = _audit_changes.get()
    return holder["changes"] if holder else None


def reset_audit_changes(token) -> None:
    _audit_changes.reset(token)


class RequestIdFilter(logging.Filter):
    """Adds ``request_id`` to every record so the log format can reference it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        inbound = headers.get(REQUEST_ID_HEADER.encode())
        request_id = inbound.decode() if inbound else uuid.uuid4().hex
        token = _request_id.set(request_id)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append(
                    (REQUEST_ID_HEADER.encode(), request_id.encode())
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _request_id.reset(token)
