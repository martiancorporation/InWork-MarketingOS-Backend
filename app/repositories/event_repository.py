"""Data access for marketing-calendar events.

Every query is hard-filtered by ``client_id`` so events can never leak across
clients — the same tenant-isolation stance the rest of the repositories take.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.enums import ApprovalStatus, EventStage, EventType, SocialPlatform
from app.models.event import MarketingEvent
from app.repositories.base import BaseRepository


class EventRepository(BaseRepository[MarketingEvent]):
    model = MarketingEvent

    def get_for_client(
        self, client_id: uuid.UUID, event_id: uuid.UUID
    ) -> MarketingEvent | None:
        """Load one event (with post/ad/assets/activity eager-loaded) scoped to a client."""
        return self.db.scalar(
            select(MarketingEvent)
            .where(
                MarketingEvent.id == event_id,
                MarketingEvent.client_id == client_id,
            )
            .options(
                selectinload(MarketingEvent.post),
                selectinload(MarketingEvent.ad),
                selectinload(MarketingEvent.assets),
                selectinload(MarketingEvent.activity),
            )
        )

    def list_for_client(
        self,
        client_id: uuid.UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        stage: EventStage | None = None,
        platform: SocialPlatform | None = None,
        type: EventType | None = None,
        approval_status: ApprovalStatus | None = None,
    ) -> list[MarketingEvent]:
        stmt = select(MarketingEvent).where(MarketingEvent.client_id == client_id)
        if start is not None:
            stmt = stmt.where(MarketingEvent.event_date >= start)
        if end is not None:
            stmt = stmt.where(MarketingEvent.event_date <= end)
        if stage is not None:
            stmt = stmt.where(MarketingEvent.stage == stage)
        if platform is not None:
            stmt = stmt.where(MarketingEvent.platform == platform)
        if type is not None:
            stmt = stmt.where(MarketingEvent.type == type)
        if approval_status is not None:
            stmt = stmt.where(MarketingEvent.approval_status == approval_status)
        stmt = stmt.order_by(
            MarketingEvent.event_date.asc(), MarketingEvent.event_time.asc()
        )
        return list(self.db.scalars(stmt).all())
