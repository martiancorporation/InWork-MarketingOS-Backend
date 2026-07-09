"""Client onboarding use-cases.

Two ways in, sharing the same object-graph builders:

* **Atomic** (``onboard``) — the whole wizard payload in one transaction. Kept
  for scripts/imports and backward compatibility.
* **Progressive** — the web wizard's real flow: ``create_draft`` opens a draft
  client at step 1 (the mandatory gate), then ``update_step`` autosaves each
  later step partially, ``add_documents`` attaches uploads, and ``complete``
  finalizes. Every progressive call recomputes readiness at the router.

Repositories never commit; this service owns the transaction boundary.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.client import Client, ClientBrandColor, ClientBrandFont, ClientPlatform
from app.models.compliance import ComplianceEntry
from app.models.contact import ClientContact
from app.models.document import Document
from app.models.enums import ClientPipelineStage, ClientStatus, ComplianceKind, ContactSide
from app.models.user import User
from app.repositories.client_repository import ClientRepository
from app.schemas.onboarding import (
    BasicsUpdate,
    BrandUpdate,
    ComplianceIn,
    ContactIn,
    DocumentRef,
    OnboardingDraftRequest,
    OnboardingProgress,
    OnboardingRequest,
    OnboardingStepUpdate,
)
from app.utils.slug import slugify, unique_slug

FINAL_STEP = 8
_BASIC_FIELDS = ("name", "business_type", "industry", "website", "language", "location", "markets")
_BRAND_SCALARS = ("about_brand", "brand_voice", "brand_extracted", "color_guidelines", "logo_url")


class OnboardingService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.clients = ClientRepository(db)

    # ---- atomic (whole payload at once) ----

    def onboard(self, admin: User, data: OnboardingRequest) -> Client:
        slug = unique_slug(
            slugify(data.name, fallback="client"), exists=self.clients.slug_exists
        )

        client = Client(
            created_by=admin.id,
            slug=slug,
            name=data.name,
            business_type=data.business_type,
            industry=data.industry,
            website=data.website,
            language=data.language,
            location=data.location,
            markets=data.markets,
            about_brand=data.brand.about_brand,
            brand_voice=data.brand.brand_voice,
            brand_extracted=data.brand.brand_extracted,
            color_guidelines=data.brand.color_guidelines,
            logo_url=data.brand.logo_url,
            goals=data.goals,
            status=ClientStatus.onboarding,
            pipeline_stage=ClientPipelineStage.onboarding,
            onboarding_step=FINAL_STEP,  # created complete in one shot
        )

        client.brand_colors = [
            ClientBrandColor(hex=c.hex, label=c.label, position=i)
            for i, c in enumerate(data.brand.colors)
        ]
        client.brand_fonts = [
            ClientBrandFont(family=f) for f in data.brand.fonts if f.strip()
        ]
        client.platforms = [ClientPlatform(channel=ch) for ch in data.platforms]
        client.contacts = [
            *self._contacts(data.client_contacts, ContactSide.client),
            *self._contacts(data.inwork_contacts, ContactSide.inwork),
        ]

        if data.compliance.feed and data.compliance.feed.strip():
            client.compliance_entries = [
                ComplianceEntry(
                    kind=ComplianceKind.note,
                    text=data.compliance.feed.strip(),
                    author_id=admin.id,
                )
            ]

        client.documents = [
            Document(
                kind=d.kind,
                name=d.name,
                mime_type=d.mime_type,
                size_bytes=d.size_bytes,
                storage_url=d.storage_url,
                uploaded_by=admin.id,
            )
            for d in data.documents
        ]

        self.clients.add(client)
        self._commit("Could not create client — please retry.")
        self.db.refresh(client)
        return client

    # ---- progressive (step-by-step) ----

    def get(self, client_id: uuid.UUID) -> Client:
        """Load a client for an admin onboarding operation (admin sees all)."""
        client = self.clients.get(client_id)
        if client is None:
            raise NotFoundError("Client not found.")
        return client

    def create_draft(self, admin: User, data: OnboardingDraftRequest) -> Client:
        """Step 1 gate — open a draft client from the mandatory basics."""
        slug = unique_slug(
            slugify(data.name, fallback="client"), exists=self.clients.slug_exists
        )
        client = Client(
            created_by=admin.id,
            slug=slug,
            name=data.name,
            business_type=data.business_type,
            industry=data.industry,
            website=data.website,
            language=data.language,
            location=data.location,
            markets=data.markets,
            status=ClientStatus.onboarding,
            pipeline_stage=ClientPipelineStage.onboarding,
            onboarding_step=1,
        )
        self.clients.add(client)
        self._commit("Could not start onboarding — please retry.")
        self.db.refresh(client)
        return client

    def update_step(
        self, admin: User, client: Client, data: OnboardingStepUpdate
    ) -> Client:
        """Apply a partial step save. Only the sections present are written."""
        sent = data.model_fields_set

        if "basics" in sent and data.basics is not None:
            self._apply_basics(client, data.basics)
        if "brand" in sent and data.brand is not None:
            self._apply_brand(client, data.brand)
        if "platforms" in sent and data.platforms is not None:
            client.platforms = [ClientPlatform(channel=ch) for ch in data.platforms]
        if "goals" in sent:
            client.goals = data.goals
        if "compliance" in sent and data.compliance is not None:
            self._apply_compliance(client, data.compliance, admin)
        if "client_contacts" in sent and data.client_contacts is not None:
            self._replace_contacts(client, data.client_contacts, ContactSide.client)
        if "inwork_contacts" in sent and data.inwork_contacts is not None:
            self._replace_contacts(client, data.inwork_contacts, ContactSide.inwork)

        if data.step is not None:
            client.onboarding_step = max(client.onboarding_step, data.step)

        self._commit("Could not save onboarding step — please retry.")
        self.db.refresh(client)
        return client

    def add_documents(
        self, admin: User, client: Client, documents: list[DocumentRef]
    ) -> Client:
        """Attach uploaded document references (step 7)."""
        for d in documents:
            client.documents.append(
                Document(
                    kind=d.kind,
                    name=d.name,
                    mime_type=d.mime_type,
                    size_bytes=d.size_bytes,
                    storage_url=d.storage_url,
                    uploaded_by=admin.id,
                )
            )
        self._commit("Could not attach documents — please retry.")
        self.db.refresh(client)
        return client

    def complete(self, client: Client) -> Client:
        """Finalize the wizard (step 8). Status stays ``onboarding`` until the
        first integration connects; the step tracker marks the form done."""
        client.onboarding_step = FINAL_STEP
        self._commit("Could not finalize onboarding — please retry.")
        self.db.refresh(client)
        return client

    @staticmethod
    def progress(client: Client) -> OnboardingProgress:
        step = client.onboarding_step
        return OnboardingProgress(
            step=step,
            total_steps=FINAL_STEP,
            percent=int(step / FINAL_STEP * 100 + 0.5),  # 1→13 … 8→100
            completed=step >= FINAL_STEP,
        )

    # ---- section appliers ----

    @staticmethod
    def _apply_basics(client: Client, basics: BasicsUpdate) -> None:
        for attr in _BASIC_FIELDS:
            if attr in basics.model_fields_set:
                setattr(client, attr, getattr(basics, attr))

    @staticmethod
    def _apply_brand(client: Client, brand: BrandUpdate) -> None:
        for attr in _BRAND_SCALARS:
            if attr in brand.model_fields_set:
                setattr(client, attr, getattr(brand, attr))
        if "colors" in brand.model_fields_set and brand.colors is not None:
            client.brand_colors = [
                ClientBrandColor(hex=c.hex, label=c.label, position=i)
                for i, c in enumerate(brand.colors)
            ]
        if "fonts" in brand.model_fields_set and brand.fonts is not None:
            client.brand_fonts = [
                ClientBrandFont(family=f) for f in brand.fonts if f.strip()
            ]

    @staticmethod
    def _apply_compliance(
        client: Client, compliance: ComplianceIn, admin: User
    ) -> None:
        # The onboarding feed is a single note; replace it on each save so
        # repeated autosaves don't stack duplicates. Other entry kinds (added
        # later in the register) are preserved.
        feed = (compliance.feed or "").strip()
        kept = [e for e in client.compliance_entries if e.kind != ComplianceKind.note]
        if feed:
            kept.append(
                ComplianceEntry(
                    kind=ComplianceKind.note, text=feed, author_id=admin.id
                )
            )
        client.compliance_entries = kept

    def _replace_contacts(
        self, client: Client, contacts: list[ContactIn], side: ContactSide
    ) -> None:
        kept = [c for c in client.contacts if c.side != side]
        client.contacts = kept + self._contacts(contacts, side)

    @staticmethod
    def _contacts(contacts: list[ContactIn], side: ContactSide) -> list[ClientContact]:
        rows: list[ClientContact] = []
        for i, c in enumerate(contacts):
            if not (c.email or c.name):
                continue
            rows.append(
                ClientContact(
                    side=side,
                    name=c.name,
                    role=c.role,
                    department=c.department,
                    email=c.email,
                    phone=c.phone,
                    description=c.description,
                    is_primary=(i == 0),
                )
            )
        return rows

    def _commit(self, error_message: str) -> None:
        try:
            self.db.commit()
        except Exception:  # pragma: no cover - unique-slug race etc.
            self.db.rollback()
            raise ConflictError(error_message)
