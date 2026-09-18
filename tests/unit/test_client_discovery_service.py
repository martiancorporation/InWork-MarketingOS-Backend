"""``ClientDiscoveryService.auto_fill`` — website auto-fill for onboarding.

Covers the non-negotiable rule the whole feature exists for: fill-only-if-
empty (never clobbers a value the client already has), ``onboarding_step``
never advances (auto-fill is not auto-complete), and fields with no column to
persist into (a general contact email/phone, social links) come back as
suggestions rather than a fabricated ``ClientContact`` row.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.security import hash_password
from app.models.client import Client, ClientBrandColor, ClientBrandFont
from app.models.contact import ClientContact
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.onboarding import AutoFillRequest, BrandExtraction
from app.services.client_discovery_service import ClientDiscoveryService, _name_from_domain
from app.utils.web import IdentitySignals, SocialLink


class _FakeBrand:
    """Stands in for ``BrandExtractionService`` — returns a scripted
    ``(BrandExtraction, IdentitySignals | None, source, description)`` without
    any network call."""

    def __init__(
        self,
        result: BrandExtraction,
        identity: IdentitySignals | None,
        source: str = "scrape",
        description: str | None = None,
    ) -> None:
        self.result = result
        self.identity = identity
        self.source = source
        self.description = description
        self.calls: list[str] = []

    async def extract_with_identity(self, website: str, context=None):
        self.calls.append(website)
        return self.result, self.identity, self.source, self.description


def _admin(db: Session) -> User:
    user = User(
        email="admin@test.com",
        name="Admin",
        password_hash=hash_password("irrelevant1"),
        role=UserRole.admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _full_result() -> BrandExtraction:
    return BrandExtraction(
        summary="A friendly neighborhood garage.",
        colors=["#0D6EFD", "#FFC107"],
        fonts=["Inter"],
        tone="Warm, plain-spoken",
        imagery="Bright, candid shop photos",
        suggested_industry="Automotive Repair",
        suggested_business_type="Local Service Business",
        ai_generated=True,
    )


def _full_identity() -> IdentitySignals:
    return IdentitySignals(
        org_name="Tony's Garage",
        logo_url="https://tonysgarage.com/logo.png",
        favicon_url="https://tonysgarage.com/favicon.ico",
        location="Austin, TX",
        emails=["hello@tonysgarage.com"],
        phones=["555-0100"],
        social_links=[SocialLink(platform="instagram", url="https://instagram.com/tonysgarage")],
    )


def test_creates_a_draft_and_fills_empty_fields(db_session: Session):
    admin = _admin(db_session)
    brand = _FakeBrand(_full_result(), _full_identity())
    service = ClientDiscoveryService(db_session, brand=brand)

    result = asyncio.run(
        service.auto_fill(admin, AutoFillRequest(website="https://tonysgarage.com"))
    )

    assert result.created is True
    assert result.client.name == "Tony's Garage"
    assert result.client.industry == "Automotive Repair"
    assert result.client.business_type == "Local Service Business"
    assert result.client.location == "Austin, TX"
    assert result.client.about_brand == "A friendly neighborhood garage."
    assert result.client.brand_voice == "Warm, plain-spoken"
    assert result.client.logo_url == "https://tonysgarage.com/logo.png"
    assert [c.hex for c in result.client.brand_colors] == ["#0D6EFD", "#FFC107"]
    assert [f.family for f in result.client.brand_fonts] == ["Inter"]
    # onboarding_step is never touched by auto-fill.
    assert result.client.onboarding_step == 1
    assert "basics.industry" in result.filled
    assert "basics.business_type" in result.filled
    # A brand-new client's derived name is reported too, even though it was
    # set at creation time rather than through the fill-only-if-empty patch —
    # otherwise the frontend would never learn what name was actually saved.
    assert "basics.name" in result.filled
    assert set(result.ai_guessed) == {"basics.industry", "basics.business_type"}


def test_never_overwrites_a_value_the_client_already_has(db_session: Session):
    admin = _admin(db_session)
    client = Client(
        slug="acme",
        name="Acme (typed by operator)",
        website="https://acme.com",
        industry="Already set by hand",
        onboarding_step=1,
    )
    db_session.add(client)
    db_session.commit()
    db_session.refresh(client)

    brand = _FakeBrand(_full_result(), _full_identity())
    service = ClientDiscoveryService(db_session, brand=brand)
    result = asyncio.run(
        service.auto_fill(admin, AutoFillRequest(website="https://acme.com", client_id=client.id))
    )

    assert result.created is False
    assert result.client.name == "Acme (typed by operator)"  # untouched
    assert result.client.industry == "Already set by hand"  # untouched
    assert result.client.business_type == "Local Service Business"  # was empty, now filled
    assert "basics.name" not in result.filled
    assert "basics.industry" not in result.filled
    assert "basics.business_type" in result.filled


def test_second_run_is_a_no_op_once_everything_is_filled(db_session: Session):
    admin = _admin(db_session)
    brand = _FakeBrand(_full_result(), _full_identity())
    service = ClientDiscoveryService(db_session, brand=brand)

    first = asyncio.run(
        service.auto_fill(admin, AutoFillRequest(website="https://tonysgarage.com"))
    )
    assert first.filled  # something was written the first time

    second = asyncio.run(
        service.auto_fill(
            admin,
            AutoFillRequest(website="https://tonysgarage.com", client_id=first.client.id),
        )
    )
    assert second.filled == []
    assert second.ai_guessed == []


def test_colors_and_fonts_only_replace_an_empty_collection(db_session: Session):
    admin = _admin(db_session)
    client = Client(slug="colorco", name="Color Co", onboarding_step=1)
    db_session.add(client)
    db_session.commit()
    db_session.refresh(client)
    client.brand_colors = [ClientBrandColor(hex="#ABCDEF", position=0)]
    client.brand_fonts = [ClientBrandFont(family="Existing Font")]
    db_session.commit()

    brand = _FakeBrand(_full_result(), None)
    service = ClientDiscoveryService(db_session, brand=brand)
    result = asyncio.run(
        service.auto_fill(
            admin, AutoFillRequest(website="https://colorco.com", client_id=client.id)
        )
    )

    assert [c.hex for c in result.client.brand_colors] == ["#ABCDEF"]
    assert [f.family for f in result.client.brand_fonts] == ["Existing Font"]
    assert "brand.colors" not in result.filled
    assert "brand.fonts" not in result.filled


def test_name_falls_back_to_the_domain_when_nothing_else_is_found(db_session: Session):
    admin = _admin(db_session)
    empty_result = BrandExtraction(summary="", ai_generated=False)
    brand = _FakeBrand(empty_result, None)
    service = ClientDiscoveryService(db_session, brand=brand)

    result = asyncio.run(
        service.auto_fill(admin, AutoFillRequest(website="https://acme-co.example"))
    )

    assert result.client.name == "Acme Co"
    assert result.client.industry is None
    assert result.client.business_type is None


def test_synthesized_fallback_summary_is_never_persisted_as_about_brand(db_session: Session):
    """Regression guard: BrandExtractionService._fallback() synthesizes a
    placeholder summary ("Draft brand theme based on ...") whenever
    ai_generated is False — that placeholder must never be saved as if it
    were real about_brand content."""
    fallback_result = BrandExtraction(
        summary="Draft brand theme based on https://x.example. Review and refine before saving.",
        ai_generated=False,
    )
    brand = _FakeBrand(fallback_result, None, source="scrape", description=None)
    service = ClientDiscoveryService(db_session, brand=brand)

    result = asyncio.run(
        service.auto_fill(
            admin=_admin(db_session), data=AutoFillRequest(website="https://x.example")
        )
    )

    assert result.client.about_brand is None
    assert "brand.about_brand" not in result.filled


def test_a_real_meta_description_is_used_even_when_ai_is_unconfigured(db_session: Session):
    fallback_result = BrandExtraction(
        summary="A real og:description found on the page.", ai_generated=False
    )
    brand = _FakeBrand(
        fallback_result,
        None,
        source="scrape",
        description="A real og:description found on the page.",
    )
    service = ClientDiscoveryService(db_session, brand=brand)

    result = asyncio.run(
        service.auto_fill(
            admin=_admin(db_session), data=AutoFillRequest(website="https://x.example")
        )
    )

    assert result.client.about_brand == "A real og:description found on the page."
    assert "brand.about_brand" in result.filled


def test_name_from_domain_examples():
    assert _name_from_domain("acme-co.com") == "Acme Co"
    assert _name_from_domain("https://www.tonysgarage.com/") == "Tonysgarage"


def test_suggestions_are_returned_but_never_persisted_as_a_contact(db_session: Session):
    admin = _admin(db_session)
    brand = _FakeBrand(_full_result(), _full_identity())
    service = ClientDiscoveryService(db_session, brand=brand)

    result = asyncio.run(
        service.auto_fill(admin, AutoFillRequest(website="https://tonysgarage.com"))
    )

    assert result.suggestions.emails == ["hello@tonysgarage.com"]
    assert result.suggestions.phones == ["555-0100"]
    assert result.suggestions.social_links[0].platform == "instagram"
    assert db_session.query(ClientContact).filter_by(client_id=result.client.id).count() == 0


def test_updating_an_unknown_client_id_raises_not_found(db_session: Session):
    admin = _admin(db_session)
    brand = _FakeBrand(_full_result(), _full_identity())
    service = ClientDiscoveryService(db_session, brand=brand)

    with pytest.raises(NotFoundError):
        asyncio.run(
            service.auto_fill(
                admin, AutoFillRequest(website="https://x.example", client_id=uuid.uuid4())
            )
        )
