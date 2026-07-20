"""Cross-cutting middleware.

``AuditMiddleware`` records every API request to the ``audit_log`` table: who
(actor from the JWT), what (a stable dotted action + entity pointer derived from
the path), the outcome (status code, duration), and request context (ip, user
agent, query). It runs on its own short-lived DB session and swallows all
errors, so auditing can never break or slow-fail a request.

For write requests it also peeks at the JSON response to capture the id of a
freshly-created resource — otherwise a ``POST`` has no id in its path.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

import jwt
from starlette.requests import Request
from starlette.types import ASGIApp

from app.core.request_context import (
    begin_audit_changes,
    get_audit_changes,
    reset_audit_changes,
)
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db.session import get_session_factory
from app.services.audit_service import AuditService, derive_audit

logger = logging.getLogger(__name__)

# Infra endpoints that would just be noise in an audit trail.
_SKIP_EXACT = {"/health", "/openapi.json", "/favicon.ico"}
_SKIP_PREFIX = ("/docs", "/redoc")
_WRITE_METHODS = {"POST", "PUT", "PATCH"}
_MAX_BODY_PEEK = 100_000  # bytes; larger JSON responses aren't buffered


class AuditMiddleware:
    """Pure-ASGI middleware (works regardless of other middleware ordering)."""

    def __init__(self, app: ASGIApp, *, prefix: str = "/api/v1") -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path
        method = request.method

        if method == "OPTIONS" or path in _SKIP_EXACT or path.startswith(_SKIP_PREFIX):
            await self.app(scope, receive, send)
            return
        if not path.startswith(self.prefix):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500
        body_chunks: list[bytes] = []
        buffering = method in _WRITE_METHODS
        # Fresh per-request holder; a service may fill it with a before/after diff.
        # A mutable holder (not a plain set) so the diff survives the sync-route
        # threadpool hop back to this async middleware.
        changes_token = begin_audit_changes()

        async def send_wrapper(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body" and buffering:
                chunk = message.get("body", b"")
                if chunk and sum(len(c) for c in body_chunks) < _MAX_BODY_PEEK:
                    body_chunks.append(chunk)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            changes = get_audit_changes()
            try:
                self._record(request, method, path, status_code, duration_ms, body_chunks, changes)
            except Exception as exc:  # never let auditing break the response
                logger.warning("audit write failed for %s %s: %s", method, path, exc)
            reset_audit_changes(changes_token)

    def _record(
        self,
        request: Request,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        body_chunks: list[bytes],
        changes: dict | None = None,
    ) -> None:
        actor_id = self._actor(request)
        entity, entity_id, action = derive_audit(method, path, prefix=self.prefix)
        client_id = entity_id if entity == "clients" else None

        # For a successful create, the new id lives in the response body, not the path.
        if entity_id is None and 200 <= status_code < 300 and body_chunks:
            found = self._id_from_body(b"".join(body_chunks))
            if found is not None:
                entity_id = found
                if entity == "clients":
                    client_id = found

        meta = {
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "query": request.url.query or None,
            "ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
        }

        session_factory = get_session_factory()
        with session_factory() as db:
            AuditService(db).record(
                entity=entity,
                action=action,
                actor_user_id=actor_id,
                entity_id=entity_id,
                client_id=client_id,
                target_label=f"{method} {path}",
                meta=meta,
                changes=changes,
            )

    @staticmethod
    def _actor(request: Request) -> uuid.UUID | None:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        try:
            payload = decode_token(auth[7:])
            if payload.get("type") != TOKEN_TYPE_ACCESS:
                return None
            return uuid.UUID(str(payload.get("sub")))
        except (jwt.PyJWTError, ValueError, TypeError):
            return None

    @staticmethod
    def _id_from_body(body: bytes) -> uuid.UUID | None:
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        # Direct id, or nested under the created resource (onboarding wraps `client`).
        for candidate in (
            data.get("id"),
            (data.get("client") or {}).get("id") if isinstance(data.get("client"), dict) else None,
            (data.get("user") or {}).get("id") if isinstance(data.get("user"), dict) else None,
        ):
            if candidate:
                try:
                    return uuid.UUID(str(candidate))
                except (ValueError, TypeError):
                    continue
        return None
