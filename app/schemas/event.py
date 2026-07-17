"""Marketing-calendar schemas: events plus their post/ad satellites.

Mirrors the web calendar (month grid + "New Post" drawer) and the day view
(full event detail with the client-approval workflow). ``MarketingEvent`` is the
base calendar row; ``post`` and ``ad`` are optional 1:1 detail objects.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, Field

from app.models.enums import (
    AdObjective,
    ApprovalStatus,
    EventStage,
    EventType,
    SocialPlatform,
)
from app.schemas.common import MAX_LONG_LINE, MAX_TEXT, ORMModel

# --------------------------------------------------------------------------- #
# Sub-objects (input)
# --------------------------------------------------------------------------- #


class EventPostIn(BaseModel):
    image_url: str | None = Field(None, max_length=1024)
    caption: str | None = Field(None, max_length=MAX_TEXT)
    hashtags: str | None = Field(None, max_length=MAX_LONG_LINE)  # e.g. "#new #drop"
    cta_label: str | None = Field(None, max_length=80)  # e.g. "Book Now"
    cta_url: str | None = Field(None, max_length=1024)


class EventAdIn(BaseModel):
    budget_usd: float = Field(0, ge=0)
    objective: AdObjective = AdObjective.awareness
    audience: str | None = Field(None, max_length=MAX_TEXT)
    bid_strategy: str | None = Field(None, max_length=60)
    duration_days: int | None = Field(None, ge=1)


# --------------------------------------------------------------------------- #
# Sub-objects (read)
# --------------------------------------------------------------------------- #


class EventPostRead(ORMModel):
    image_url: str | None = None
    caption: str | None = None
    hashtags: str | None = None
    cta_label: str | None = None
    cta_url: str | None = None


class EventAdRead(ORMModel):
    budget_usd: float
    objective: AdObjective
    audience: str | None = None
    bid_strategy: str | None = None
    duration_days: int | None = None


class EventAssetRead(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    position: int


class EventActivityRead(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    action: str
    note: str | None = None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Create / update
# --------------------------------------------------------------------------- #


class EventCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    type: EventType
    platform: SocialPlatform
    event_date: date
    event_time: time
    description: str | None = Field(None, max_length=MAX_TEXT)
    strategy: str | None = Field(None, max_length=MAX_TEXT)
    stage: EventStage = EventStage.draft
    campaign_id: uuid.UUID | None = None  # group under a campaign (optional)
    post: EventPostIn | None = None
    ad: EventAdIn | None = None


class EventUpdate(BaseModel):
    """Partial autosave — only the fields present in the body are applied.

    Presence is detected via ``model_fields_set`` so patching one field never
    clears the others (mirrors the onboarding-step autosave contract).
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    type: EventType | None = None
    platform: SocialPlatform | None = None
    event_date: date | None = None
    event_time: time | None = None
    description: str | None = Field(default=None, max_length=MAX_TEXT)
    strategy: str | None = Field(default=None, max_length=MAX_TEXT)
    stage: EventStage | None = None
    campaign_id: uuid.UUID | None = None
    post: EventPostIn | None = None
    ad: EventAdIn | None = None


class ApprovalDecision(BaseModel):
    """Client-approval transition (approve / request changes / reject / resubmit).

    Setting ``status=pending`` with a note is the web's "Submit for review
    again" action; every decision is appended to the event's activity log.
    """

    status: ApprovalStatus
    note: str | None = Field(default=None, max_length=MAX_TEXT)


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #


class EventListItem(ORMModel):
    """Lightweight row for the month grid / drafts panel."""

    id: uuid.UUID
    client_id: uuid.UUID
    campaign_id: uuid.UUID | None = None
    title: str
    type: EventType
    platform: SocialPlatform
    event_date: date
    event_time: time
    stage: EventStage
    approval_status: ApprovalStatus


class EventRead(ORMModel):
    """Full event detail for the day view."""

    id: uuid.UUID
    client_id: uuid.UUID
    campaign_id: uuid.UUID | None = None
    title: str
    type: EventType
    platform: SocialPlatform
    event_date: date
    event_time: time
    description: str | None = None
    strategy: str | None = None
    stage: EventStage
    approval_status: ApprovalStatus
    approved_by: uuid.UUID | None = None
    approval_note: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    post: EventPostRead | None = None
    ad: EventAdRead | None = None
    assets: list[EventAssetRead] = []
    activity: list[EventActivityRead] = []


class EventListResponse(BaseModel):
    items: list[EventListItem]
    total: int
    page: int = 1
    page_size: int = 20
