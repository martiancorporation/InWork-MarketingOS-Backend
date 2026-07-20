"""Data access for KPI alerts. Every query is hard-filtered by ``client_id``."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.alert import Alert
from app.models.enums import AlertStatus
from app.repositories.base import BaseRepository


class AlertRepository(BaseRepository[Alert]):
    model = Alert

    def get_for_client(self, client_id: uuid.UUID, alert_id: uuid.UUID) -> Alert | None:
        return self.db.scalar(
            select(Alert).where(Alert.id == alert_id, Alert.client_id == client_id)
        )

    def list_for_client(
        self,
        client_id: uuid.UUID,
        *,
        status: str | None = None,
        severity: str | None = None,
        kind: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[Alert], int]:
        conditions = [Alert.client_id == client_id]
        if status is not None:
            conditions.append(Alert.status == status)
        if severity is not None:
            conditions.append(Alert.severity == severity)
        if kind is not None:
            conditions.append(Alert.kind == kind)
        total = self.db.scalar(select(func.count()).select_from(Alert).where(*conditions))
        stmt = select(Alert).where(*conditions).order_by(Alert.created_at.desc()).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all()), int(total or 0)

    def open_counts(self, client_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, int]:
        """Count open alerts, grouped by client.

        ``client_ids=None`` counts across every client; a list restricts the
        scope (an empty list yields no rows). Backs the cross-client
        "what's on you" view (BE-04).
        """
        conditions = [Alert.status == AlertStatus.open.value]
        if client_ids is not None:
            conditions.append(Alert.client_id.in_(client_ids))
        rows = self.db.execute(
            select(Alert.client_id, func.count()).where(*conditions).group_by(Alert.client_id)
        ).all()
        return {cid: int(n) for cid, n in rows}

    def live_for_client(self, client_id: uuid.UUID) -> list[Alert]:
        """Open + acknowledged alerts — the set an evaluation pass reconciles."""
        return list(
            self.db.scalars(
                select(Alert).where(
                    Alert.client_id == client_id,
                    Alert.status.in_([AlertStatus.open.value, AlertStatus.acknowledged.value]),
                )
            ).all()
        )
