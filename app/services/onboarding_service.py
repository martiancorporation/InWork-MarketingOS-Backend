"""Client onboarding use-case.

Turns the wizard payload into a complete ``Client`` object graph and persists it
in one transaction. SQLAlchemy cascades the children (colors, fonts, platforms,
contacts, compliance, documents) from the ``Client`` relationships.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.models.client import Client, ClientBrandColor, ClientBrandFont, ClientPlatform
from app.models.compliance import ComplianceEntry
from app.models.contact import ClientContact
from app.models.document import Document
from app.models.enums import ClientPipelineStage, ClientStatus, ComplianceKind, ContactSide
from app.models.user import User
from app.repositories.client_repository import ClientRepository
from app.repositories.organization_repository import OrganizationRepository
from app.schemas.onboarding import ContactIn, OnboardingRequest
from app.utils.slug import slugify, unique_slug


class OnboardingService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.clients = ClientRepository(db)
        self.orgs = OrganizationRepository(db)

    def onboard(self, user: User, data: OnboardingRequest) -> Client:
        from app.services.client_service import ClientService

        org = ClientService(self.db).resolve_org(user)

        base_slug = slugify(data.name, fallback="client")
        slug = unique_slug(
            base_slug, exists=lambda s: self.clients.slug_exists(org.id, s)
        )

        client = Client(
            organization_id=org.id,
            created_by=user.id,
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
                    author_id=user.id,
                )
            ]

        client.documents = [
            Document(
                kind=d.kind,
                name=d.name,
                mime_type=d.mime_type,
                size_bytes=d.size_bytes,
                storage_url=d.storage_url,
                uploaded_by=user.id,
            )
            for d in data.documents
        ]

        self.clients.add(client)
        try:
            self.db.commit()
        except Exception:  # pragma: no cover - unique-slug race etc.
            self.db.rollback()
            raise ConflictError("Could not create client — please retry.")
        self.db.refresh(client)
        return client

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
