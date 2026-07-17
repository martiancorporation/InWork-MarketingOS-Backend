"""Marketing-calendar use-cases: events + post/ad detail + approval workflow.

Client-access scoping is enforced at the router (via ``ClientService.get_client``)
before any method here runs, so these methods take a ``client_id`` that the
caller is already allowed to see and hard-filter every query by it.

Transaction discipline follows the house rule: the repository only flushes; this
service owns the commit so an event and its satellites/activity save atomically.
"""

from __future__ import annotations

import calendar as _calendar
import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.pagination import PaginationParams
from app.models.enums import ApprovalStatus, EventStage, EventType, SocialPlatform
from app.models.event import EventActivity, EventAd, EventPost, MarketingEvent
from app.repositories.event_repository import EventRepository
from app.schemas.event import (
    ApprovalDecision,
    EventAdIn,
    EventCreate,
    EventListItem,
    EventListResponse,
    EventPostIn,
    EventUpdate,
)


class CalendarService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.events = EventRepository(db)

    # ---- reads --------------------------------------------------------- #

    def list_events(
        self,
        client_id: uuid.UUID,
        *,
        pagination: PaginationParams,
        year: int | None = None,
        month: int | None = None,
        stage: EventStage | None = None,
        platform: SocialPlatform | None = None,
        type: EventType | None = None,
        approval_status: ApprovalStatus | None = None,
    ) -> EventListResponse:
        start, end = self._month_range(year, month)
        rows, total = self.events.list_for_client(
            client_id,
            start=start,
            end=end,
            stage=stage,
            platform=platform,
            type=type,
            approval_status=approval_status,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        items = [EventListItem.model_validate(e) for e in rows]
        return EventListResponse(
            items=items,
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def get_event(self, client_id: uuid.UUID, event_id: uuid.UUID) -> MarketingEvent:
        event = self.events.get_for_client(client_id, event_id)
        if event is None:
            raise NotFoundError("Event not found.")
        return event

    # ---- writes -------------------------------------------------------- #

    def create_event(
        self, client_id: uuid.UUID, data: EventCreate, *, created_by: uuid.UUID
    ) -> MarketingEvent:
        event = MarketingEvent(
            client_id=client_id,
            campaign_id=data.campaign_id,
            title=data.title,
            type=data.type,
            platform=data.platform,
            event_date=data.event_date,
            event_time=data.event_time,
            description=data.description,
            strategy=data.strategy,
            stage=data.stage,
            created_by=created_by,
        )
        if data.post is not None:
            event.post = self._build_post(data.post)
        if data.ad is not None:
            event.ad = self._build_ad(data.ad)
        self.events.add(event)
        self.events.flush()  # assign the id before logging activity
        self._log(event, "created", None, user_id=created_by)
        self.db.commit()
        return self.get_event(client_id, event.id)

    def update_event(
        self,
        client_id: uuid.UUID,
        event_id: uuid.UUID,
        data: EventUpdate,
        *,
        actor_id: uuid.UUID,
    ) -> MarketingEvent:
        event = self.get_event(client_id, event_id)
        fields = data.model_fields_set
        for attr in (
            "title",
            "type",
            "platform",
            "event_date",
            "event_time",
            "description",
            "strategy",
            "stage",
            "campaign_id",
        ):
            if attr in fields:
                setattr(event, attr, getattr(data, attr))
        if "post" in fields and data.post is not None:
            self._apply_post(event, data.post)
        if "ad" in fields and data.ad is not None:
            self._apply_ad(event, data.ad)
        self._log(event, "edit", None, user_id=actor_id)
        self.db.commit()
        return self.get_event(client_id, event.id)

    def decide_approval(
        self,
        client_id: uuid.UUID,
        event_id: uuid.UUID,
        data: ApprovalDecision,
        *,
        actor_id: uuid.UUID,
    ) -> MarketingEvent:
        event = self.get_event(client_id, event_id)
        event.approval_status = data.status
        event.approval_note = data.note
        # ``approved_by`` only carries meaning while the item is approved.
        event.approved_by = actor_id if data.status == ApprovalStatus.approved else None
        note = f"{data.status.value}" + (f": {data.note}" if data.note else "")
        self._log(event, "status_change", note, user_id=actor_id)
        self.db.commit()
        return self.get_event(client_id, event.id)

    def delete_event(self, client_id: uuid.UUID, event_id: uuid.UUID) -> None:
        event = self.get_event(client_id, event_id)
        self.db.delete(event)
        self.db.commit()

    # ---- helpers ------------------------------------------------------- #

    @staticmethod
    def _build_post(data: EventPostIn) -> EventPost:
        return EventPost(
            image_url=data.image_url,
            caption=data.caption,
            hashtags=data.hashtags,
            cta_label=data.cta_label,
            cta_url=data.cta_url,
        )

    @staticmethod
    def _build_ad(data: EventAdIn) -> EventAd:
        return EventAd(
            budget_usd=data.budget_usd,
            objective=data.objective,
            audience=data.audience,
            bid_strategy=data.bid_strategy,
            duration_days=data.duration_days,
        )

    def _apply_post(self, event: MarketingEvent, data: EventPostIn) -> None:
        if event.post is None:
            event.post = self._build_post(data)
        else:
            event.post.image_url = data.image_url
            event.post.caption = data.caption
            event.post.hashtags = data.hashtags
            event.post.cta_label = data.cta_label
            event.post.cta_url = data.cta_url

    def _apply_ad(self, event: MarketingEvent, data: EventAdIn) -> None:
        if event.ad is None:
            event.ad = self._build_ad(data)
        else:
            event.ad.budget_usd = data.budget_usd
            event.ad.objective = data.objective
            event.ad.audience = data.audience
            event.ad.bid_strategy = data.bid_strategy
            event.ad.duration_days = data.duration_days

    def _log(
        self,
        event: MarketingEvent,
        action: str,
        note: str | None,
        *,
        user_id: uuid.UUID | None,
    ) -> None:
        event.activity.append(
            EventActivity(action=action, note=note, user_id=user_id)
        )

    @staticmethod
    def _month_range(
        year: int | None, month: int | None
    ) -> tuple[date | None, date | None]:
        """Turn an optional (year, month) into an inclusive date window.

        ``month`` is 1-12. Passing only one of the two is treated as no filter.
        """
        if year is None or month is None:
            return None, None
        last_day = _calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_day)
