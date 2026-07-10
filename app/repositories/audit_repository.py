"""Audit-log data access — writes new entries and lists them with filters."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.sql import Select

from app.models.audit import AuditLog
from app.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditLog]):
    model = AuditLog

    def list(
        self,
        *,
        offset: int,
        limit: int,
        action: str | None = None,
        entity: str | None = None,
        actor_user_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
    ) -> tuple[list[AuditLog], int]:
        base: Select = select(AuditLog)
        if action:
            base = base.where(AuditLog.action.ilike(f"%{action.strip()}%"))
        if entity:
            base = base.where(AuditLog.entity == entity)
        if actor_user_id is not None:
            base = base.where(AuditLog.actor_user_id == actor_user_id)
        if client_id is not None:
            base = base.where(AuditLog.client_id == client_id)

        total = int(
            self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        rows = list(
            self.db.scalars(
                base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return rows, total
