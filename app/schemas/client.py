"""Client read/list response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import ClientPipelineStage, ClientStatus, ContactSide
from app.schemas.common import ORMModel


class ClientListItem(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    business_type: str | None = None
    industry: str | None = None
    website: str | None = None
    location: str | None = None
    status: ClientStatus
    spend: float
    leads: int
    cpl: float
    created_at: datetime


class ClientListResponse(BaseModel):
    items: list[ClientListItem]
    total: int
    page: int
    page_size: int


# ---- Detailed read (nested) ----

class BrandColorRead(ORMModel):
    hex: str
    label: str | None = None
    position: int


class BrandFontRead(ORMModel):
    family: str
    usage: str | None = None


class PlatformRead(ORMModel):
    channel: str


class ContactRead(ORMModel):
    side: ContactSide
    name: str
    role: str | None = None
    department: str | None = None
    email: str | None = None
    phone: str | None = None
    description: str | None = None
    is_primary: bool


class ClientRead(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    business_type: str | None = None
    industry: str | None = None
    website: str | None = None
    location: str | None = None
    language: str | None = None
    timezone: str | None = None
    markets: str | None = None
    about_brand: str | None = None
    brand_voice: str | None = None
    brand_extracted: str | None = None
    color_guidelines: str | None = None
    logo_url: str | None = None
    goals: str | None = None
    status: ClientStatus
    pipeline_stage: ClientPipelineStage
    created_at: datetime

    brand_colors: list[BrandColorRead] = []
    brand_fonts: list[BrandFontRead] = []
    platforms: list[PlatformRead] = []
    contacts: list[ContactRead] = []
