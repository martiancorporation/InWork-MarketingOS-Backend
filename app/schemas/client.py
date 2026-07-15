"""Client read/list response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, computed_field

from app.models.enums import ClientPipelineStage, ClientStatus, ContactSide
from app.schemas.common import ORMModel

# Total wizard steps — mirrors ``OnboardingService.FINAL_STEP``. Kept here so the
# schema layer doesn't import a service (respecting the dependency direction).
_ONBOARDING_TOTAL_STEPS = 8


def _percent(step: int) -> int:
    """Wizard completion as a whole-number percent (1→13 … 8→100)."""
    return int(step / _ONBOARDING_TOTAL_STEPS * 100 + 0.5)


class ClientListItem(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    business_type: str | None = None
    industry: str | None = None
    website: str | None = None
    location: str | None = None
    status: ClientStatus
    onboarding_step: int = 1
    spend: float
    leads: int
    cpl: float
    created_at: datetime

    @computed_field
    @property
    def onboarding_percent(self) -> int:
        """Progress-bar value for the list (e.g. 50, 63, 100)."""
        return _percent(self.onboarding_step)

    @computed_field
    @property
    def onboarding_completed(self) -> bool:
        return self.onboarding_step >= _ONBOARDING_TOTAL_STEPS


class ClientListResponse(BaseModel):
    items: list[ClientListItem]
    total: int
    page: int
    page_size: int


class ClientUpdate(BaseModel):
    """Partial client update (admin) — change status or basic profile fields.

    Only the fields present in the request body are applied.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    business_type: str | None = None
    industry: str | None = None
    website: str | None = None
    location: str | None = None
    status: ClientStatus | None = None


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
    onboarding_step: int = 1
    created_at: datetime

    brand_colors: list[BrandColorRead] = []
    brand_fonts: list[BrandFontRead] = []
    platforms: list[PlatformRead] = []
    contacts: list[ContactRead] = []

    @computed_field
    @property
    def onboarding_percent(self) -> int:
        return _percent(self.onboarding_step)

    @computed_field
    @property
    def onboarding_completed(self) -> bool:
        return self.onboarding_step >= _ONBOARDING_TOTAL_STEPS
