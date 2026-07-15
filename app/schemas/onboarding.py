"""Client onboarding: request payload, AI brand-extraction, and response.

Mirrors the 8-step onboarding wizard (basics → brand → platforms → goals →
compliance → contacts → documents → review) and its AI-assisted brand step.
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.models.enums import DocumentKind
from app.schemas.client import ClientRead
from app.schemas.common import MAX_LONG_LINE, MAX_TEXT, StrictModel
from app.schemas.intelligence import IntelligenceStatus

HEX_PATTERN = r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"


class BrandColorIn(StrictModel):
    hex: str = Field(pattern=HEX_PATTERN)
    label: str | None = Field(default=None, max_length=60)


class BrandIn(StrictModel):
    about_brand: str | None = Field(default=None, max_length=MAX_TEXT)
    brand_voice: str = Field(min_length=1, max_length=MAX_LONG_LINE)  # required in step 2
    brand_extracted: str | None = Field(default=None, max_length=MAX_TEXT)
    colors: list[BrandColorIn] = Field(default_factory=list, max_length=24)
    fonts: list[str] = Field(default_factory=list, max_length=12)
    color_guidelines: str | None = Field(default=None, max_length=MAX_TEXT)
    logo_url: str | None = Field(default=None, max_length=1024)


class ContactIn(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=MAX_LONG_LINE)


class ComplianceIn(StrictModel):
    # Free-form feed; the AI derives structured rules from this later.
    feed: str | None = Field(default=None, max_length=MAX_TEXT)


class DocumentRef(StrictModel):
    """A reference to an already-uploaded document (upload is a separate step)."""

    name: str = Field(min_length=1, max_length=255)
    kind: DocumentKind = DocumentKind.other
    size_bytes: int = Field(default=0, ge=0)
    mime_type: str | None = Field(default=None, max_length=120)
    storage_url: str = Field(min_length=1, max_length=1024)


class OnboardingRequest(StrictModel):
    # Step 1 — basics
    name: str = Field(min_length=1, max_length=160)
    business_type: str = Field(min_length=1, max_length=120)
    industry: str = Field(min_length=1, max_length=120)
    website: str | None = Field(default=None, max_length=255)
    language: str | None = Field(default=None, max_length=60)
    location: str | None = Field(default=None, max_length=160)
    markets: str | None = Field(default=None, max_length=MAX_LONG_LINE)

    # Step 2 — brand
    brand: BrandIn

    # Step 3 — platforms (channel ids; at least one)
    platforms: list[str] = Field(min_length=1, max_length=32)

    # Step 4 — goals
    goals: str | None = Field(default=None, max_length=MAX_TEXT)

    # Step 5 — compliance
    compliance: ComplianceIn = Field(default_factory=ComplianceIn)

    # Step 6 — contacts (at least one client contact with an email)
    client_contacts: list[ContactIn] = Field(default_factory=list)
    inwork_contacts: list[ContactIn] = Field(default_factory=list)

    # Step 7 — documents (already uploaded; optional)
    documents: list[DocumentRef] = Field(default_factory=list)

    @field_validator("platforms")
    @classmethod
    def _dedupe_platforms(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for raw in value:
            channel = raw.strip().lower()
            if channel and channel not in seen:
                seen.append(channel)
        if not seen:
            raise ValueError("Select at least one marketing platform.")
        return seen

    @model_validator(mode="after")
    def _require_primary_client_contact(self) -> OnboardingRequest:
        if not any(c.email for c in self.client_contacts):
            raise ValueError("At least one client contact with an email is required.")
        return self


# ---- Progressive (step-by-step) onboarding ----
#
# The web wizard no longer submits one giant payload at the end. Step 1 is a
# mandatory gate that creates a draft client; every step after it autosaves a
# partial update. These schemas back that flow: a draft-create request, a
# partial per-step update (only the sections present are applied), a document
# attach request, and a response that always carries the recomputed readiness
# score plus the wizard's progress.


class BasicsUpdate(StrictModel):
    """Step 1 fields, all optional — only the ones sent are written."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    business_type: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=120)
    website: str | None = Field(default=None, max_length=255)
    language: str | None = Field(default=None, max_length=60)
    location: str | None = Field(default=None, max_length=160)
    markets: str | None = Field(default=None, max_length=MAX_LONG_LINE)


class BrandUpdate(StrictModel):
    """Step 2 fields, all optional (no required ``brand_voice``) so the brand
    step can autosave before it's complete — e.g. right after AI extraction."""

    about_brand: str | None = Field(default=None, max_length=MAX_TEXT)
    brand_voice: str | None = Field(default=None, max_length=MAX_LONG_LINE)
    brand_extracted: str | None = Field(default=None, max_length=MAX_TEXT)
    colors: list[BrandColorIn] | None = Field(default=None, max_length=24)
    fonts: list[str] | None = Field(default=None, max_length=12)
    color_guidelines: str | None = Field(default=None, max_length=MAX_TEXT)
    logo_url: str | None = Field(default=None, max_length=1024)


class OnboardingDraftRequest(StrictModel):
    """Step 1 — the mandatory gate. Creates the draft client record and returns
    its id; every later step saves against that id."""

    name: str = Field(min_length=1, max_length=160)
    business_type: str = Field(min_length=1, max_length=120)
    industry: str = Field(min_length=1, max_length=120)
    website: str | None = Field(default=None, max_length=255)
    language: str | None = Field(default=None, max_length=60)
    location: str | None = Field(default=None, max_length=160)
    markets: str | None = Field(default=None, max_length=MAX_LONG_LINE)


class OnboardingStepUpdate(StrictModel):
    """Partial autosave for a step after the gate.

    Only the sections explicitly present in the body are applied — everything
    else is left untouched, so saving step 4 never wipes step 2. ``step`` is the
    wizard step being completed; it advances ``onboarding_step`` monotonically.
    """

    step: int | None = Field(default=None, ge=1, le=8)
    basics: BasicsUpdate | None = None
    brand: BrandUpdate | None = None
    platforms: list[str] | None = Field(default=None, max_length=32)
    goals: str | None = Field(default=None, max_length=MAX_TEXT)
    compliance: ComplianceIn | None = None
    client_contacts: list[ContactIn] | None = Field(default=None, max_length=100)
    inwork_contacts: list[ContactIn] | None = Field(default=None, max_length=100)

    @field_validator("platforms")
    @classmethod
    def _dedupe_platforms(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        seen: list[str] = []
        for raw in value:
            channel = raw.strip().lower()
            if channel and channel not in seen:
                seen.append(channel)
        return seen


class DocumentsRequest(StrictModel):
    """Step 7 — attach already-uploaded document references to the client."""

    documents: list[DocumentRef] = Field(min_length=1, max_length=100)


# ---- AI-assisted brand extraction ----

class BrandExtractionRequest(StrictModel):
    website: str = Field(min_length=1, max_length=255)


class BrandExtraction(BaseModel):
    summary: str
    colors: list[str] = []
    fonts: list[str] = []
    tone: str | None = None
    imagery: str | None = None
    ai_generated: bool  # False when returned by the deterministic dev fallback


# ---- Onboarding response ----

class ReadinessItem(BaseModel):
    key: str
    label: str
    weight: int


class ReadinessReport(BaseModel):
    score: int  # 0-100
    completed: list[str]
    missing: list[ReadinessItem]


class OnboardingResponse(BaseModel):
    client: ClientRead
    readiness: ReadinessReport
    intelligence: IntelligenceStatus | None = None


class OnboardingProgress(BaseModel):
    step: int  # highest step completed, 1..8
    total_steps: int = 8
    percent: int  # 0-100, matches the wizard's step meter
    completed: bool  # True once the wizard is finalized (step 8)


class OnboardingStepResponse(BaseModel):
    """Returned by every progressive endpoint: the client, the recomputed
    readiness score, where the wizard now stands, and the async intelligence
    build status (so the UI can show "analyzing…" without blocking)."""

    client: ClientRead
    readiness: ReadinessReport
    onboarding: OnboardingProgress
    intelligence: IntelligenceStatus | None = None
