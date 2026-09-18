"""Website auto-fill for client onboarding.

One scan of the client's website (reusing the existing SSRF-hardened fetch
pipeline + brand-extraction AI call, see ``app/ai/brand_extraction.py``) feeds
two kinds of onboarding data:

- **Deterministic identity facts** (name, logo, location, contact info, social
  links) pulled straight from the page's JSON-LD/Open Graph/anchor links.
- **AI-interpreted fields** (brand voice/story, plus a low-confidence
  industry/business-type guess) from the same model call the brand-extraction
  feature already makes.

Everything actually written goes through ``OnboardingService.update_step()``
with ``step`` left unset — the existing, already-tested mechanism by which
saving step data never advances ``onboarding_step``. Auto-fill never
overwrites a field the client record already has a value for (fill-only-if-
empty), and two kinds of signal that have no column to persist into today
(a general company email/phone, and social profile links) are returned as
suggestions rather than fabricating a record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.ai.brand_extraction import BrandExtractionService
from app.ai.features import AiFeature
from app.ai.usage import AiUsageContext
from app.models.client import Client
from app.models.user import User
from app.schemas.onboarding import (
    AutoFillRequest,
    AutoFillSuggestions,
    BasicsUpdate,
    BrandExtraction,
    BrandUpdate,
    OnboardingStepUpdate,
    SocialLinkOut,
)
from app.services.onboarding_service import OnboardingService
from app.utils.web import IdentitySignals, normalize_url

# Fields the AI industry/business-type guess writes — used to compute
# `ai_guessed` as the subset of `filled` that came from the model rather than
# a deterministic page signal.
_AI_GUESSED_FIELDS = frozenset({"basics.industry", "basics.business_type"})


@dataclass
class AutoFillResult:
    client: Client
    created: bool
    filled: list[str] = field(default_factory=list)
    ai_guessed: list[str] = field(default_factory=list)
    suggestions: AutoFillSuggestions = field(default_factory=AutoFillSuggestions)
    source: str = "none"
    ai_generated: bool = False


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _name_from_domain(website: str) -> str:
    """Last-resort client name when nothing else is available — derived
    literally from the domain (never invented). ``"acme-co.com"`` -> ``"Acme
    Co"``."""
    host = urlparse(normalize_url(website) or website).hostname or website
    if host.startswith("www."):
        host = host[4:]
    label = host.split(".")[0] if host else website
    words = [w for w in label.replace("_", "-").split("-") if w]
    return " ".join(w.capitalize() for w in words) or "New Client"


class ClientDiscoveryService:
    def __init__(self, db: Session, brand: BrandExtractionService | None = None) -> None:
        self.db = db
        self.onboarding = OnboardingService(db)
        self.brand = brand or BrandExtractionService()

    async def auto_fill(self, admin: User, data: AutoFillRequest) -> AutoFillResult:
        context = AiUsageContext(
            feature=AiFeature.BRAND_EXTRACTION,
            user_id=admin.id,
            meta={"website": data.website, "auto_fill": "true"},
        )
        result, identity, source, description = await self.brand.extract_with_identity(
            data.website, context
        )

        created = False
        if data.client_id is not None:
            client = self.onboarding.get(data.client_id)
        else:
            name = (
                (data.name or "").strip()
                or (identity.org_name if identity else None)
                or _name_from_domain(data.website)
            )
            client = self.onboarding.create_draft_minimal(admin, name=name, website=data.website)
            created = True

        basics = self._basics_patch(client, data, result, identity)
        brand_patch = self._brand_patch(client, result, identity, description)

        if basics or brand_patch:
            update = OnboardingStepUpdate(
                **({"basics": BasicsUpdate(**basics)} if basics else {}),
                **({"brand": BrandUpdate(**brand_patch)} if brand_patch else {}),
            )
            # `step` is never set — this is exactly what keeps auto-fill from
            # ever advancing `onboarding_step` (see OnboardingService.update_step).
            client = self.onboarding.update_step(admin, client, update)

        filled = [f"basics.{k}" for k in basics] + [f"brand.{k}" for k in brand_patch]
        if created and "basics.name" not in filled:
            # `create_draft_minimal` already set `name` at creation time (from
            # the user-typed name, the site's own identity, or the domain as a
            # last resort), so the "currently empty" check in `_basics_patch`
            # never sees it as a field to fill — but the frontend still needs
            # to learn what name was actually saved for a brand-new client.
            filled.insert(0, "basics.name")
        ai_guessed = [f for f in filled if f in _AI_GUESSED_FIELDS]

        return AutoFillResult(
            client=client,
            created=created,
            filled=filled,
            ai_guessed=ai_guessed,
            suggestions=self._suggestions(identity),
            source=source,
            ai_generated=result.ai_generated,
        )

    @staticmethod
    def _basics_patch(
        client: Client,
        data: AutoFillRequest,
        result: BrandExtraction,
        identity: IdentitySignals | None,
    ) -> dict:
        patch: dict = {}
        if _is_empty(client.name):
            name = (data.name or "").strip() or (identity.org_name if identity else None)
            if name:
                patch["name"] = name
        if _is_empty(client.website):
            patch["website"] = data.website
        if _is_empty(client.location) and identity and identity.location:
            patch["location"] = identity.location
        if _is_empty(client.industry) and result.suggested_industry:
            patch["industry"] = result.suggested_industry
        if _is_empty(client.business_type) and result.suggested_business_type:
            patch["business_type"] = result.suggested_business_type
        return patch

    @staticmethod
    def _brand_patch(
        client: Client,
        result: BrandExtraction,
        identity: IdentitySignals | None,
        description: str | None,
    ) -> dict:
        patch: dict = {}
        # `result.summary` is a synthesized placeholder ("Draft brand theme
        # based on ...") whenever `ai_generated` is False — never persist that
        # as real content. The page's own raw meta description is genuine data
        # and safe to use either way.
        about_brand = result.summary if result.ai_generated else description
        if _is_empty(client.about_brand) and about_brand:
            patch["about_brand"] = about_brand
        if _is_empty(client.brand_voice) and result.tone:
            patch["brand_voice"] = result.tone
        if _is_empty(client.logo_url) and identity and identity.logo_url:
            patch["logo_url"] = identity.logo_url
        # Colors/fonts are whole-collection fields (`_apply_brand` replaces the
        # entire list) — only ever proposed when the client currently has none.
        if not client.brand_colors and result.colors:
            patch["colors"] = [{"hex": c} for c in result.colors[:4]]
        if not client.brand_fonts and result.fonts:
            patch["fonts"] = result.fonts[:6]
        return patch

    @staticmethod
    def _suggestions(identity: IdentitySignals | None) -> AutoFillSuggestions:
        if identity is None:
            return AutoFillSuggestions()
        return AutoFillSuggestions(
            emails=list(identity.emails),
            phones=list(identity.phones),
            social_links=[
                SocialLinkOut(platform=link.platform, url=link.url)
                for link in identity.social_links
            ],
        )
