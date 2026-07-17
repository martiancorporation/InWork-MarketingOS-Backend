"""Audit trail use-cases: record an entry and query the log.

``record`` is the single programmatic entry point — the AuditMiddleware calls it
for every API request, and services can call it for richer semantic events. It
owns its own commit so an audit write never depends on (or corrupts) the caller's
transaction. ``derive_audit`` turns an HTTP method + path into a stable, dotted
action string and a structured entity pointer, so the log is populated
consistently without per-route wiring.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from app.core.pagination import PaginationParams
from app.models.audit import AuditLog
from app.repositories.audit_repository import AuditRepository
from app.schemas.audit import AuditLogListResponse, AuditLogRead

_VERB = {
    "GET": "read",
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def derive_audit(
    method: str, path: str, *, prefix: str = "/api/v1"
) -> tuple[str, uuid.UUID | None, str]:
    """Return ``(entity, entity_id, action)`` for a request.

    ``GET /api/v1/clients/{uuid}/assignments`` → ``("clients", uuid,
    "clients.assignments.read")``. UUIDs are stripped from the action but the
    first one becomes ``entity_id``.
    """
    trimmed = path[len(prefix):] if path.startswith(prefix) else path
    raw = [s for s in trimmed.strip("/").split("/") if s]

    entity_id: uuid.UUID | None = None
    segments: list[str] = []
    for seg in raw:
        if _UUID_RE.match(seg):
            if entity_id is None:
                entity_id = uuid.UUID(seg)
            continue
        segments.append(seg)

    entity = segments[0] if segments else "root"
    verb = _VERB.get(method.upper(), method.lower())
    action = ".".join([*segments, verb]) if segments else verb
    return entity, entity_id, action


def _json_safe(value: object) -> object:
    """JSON-safe scalar for an audit diff (enum → ``.value``)."""
    return getattr(value, "value", value)


def field_changes(before: dict, after: dict) -> dict | None:
    """Per-field ``{field: {before, after}}`` diff, or None if nothing changed.

    Values are compared as-is; callers pass JSON-safe scalars (enum → ``.value``).
    """
    changed = {
        key: {"before": before.get(key), "after": after.get(key)}
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key)
    }
    return changed or None


def created_changes(values: dict) -> dict | None:
    """Diff for a **create**: each provided field goes ``None → value`` so the
    audit row shows *what was added*. Skips fields whose value is None."""
    changed = {
        k: {"before": None, "after": _json_safe(v)} for k, v in values.items() if v is not None
    }
    return changed or None


def deleted_changes(values: dict) -> dict | None:
    """Diff for a **delete**: each provided field goes ``value → None`` so the
    audit row shows *what was removed* (the data is gone afterwards)."""
    changed = {
        k: {"before": _json_safe(v), "after": None} for k, v in values.items() if v is not None
    }
    return changed or None


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = AuditRepository(db)

    def record(
        self,
        *,
        entity: str,
        action: str,
        actor_user_id: uuid.UUID | None = None,
        entity_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
        target_label: str | None = None,
        meta: dict | None = None,
        changes: dict | None = None,
    ) -> AuditLog:
        row = AuditLog(
            actor_user_id=actor_user_id,
            client_id=client_id,
            entity=entity,
            entity_id=entity_id,
            action=action,
            target_label=target_label,
            meta=meta,
            changes=changes,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def list(
        self,
        pagination: PaginationParams,
        *,
        action: str | None = None,
        entity: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
    ) -> AuditLogListResponse:
        rows, total = self.repo.list(
            offset=pagination.offset,
            limit=pagination.limit,
            action=action,
            entity=entity,
            actor_user_id=actor_user_id,
            client_id=client_id,
        )
        return AuditLogListResponse(
            items=[AuditLogRead.model_validate(r) for r in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )
