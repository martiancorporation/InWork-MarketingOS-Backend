"""Schemas for the pre-publish AI content review (brand voice + SEO + compliance)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import SocialPlatform
from app.schemas.common import MAX_TEXT, StrictModel


class ContentReviewRequest(StrictModel):
    content: str = Field(min_length=1, max_length=MAX_TEXT)
    platform: SocialPlatform | None = None


class SeoScore(BaseModel):
    score: int = Field(ge=0, le=100)
    findings: list[str] = []


class ComplianceCheck(BaseModel):
    passed: bool
    violations: list[str] = []  # active "banned" terms found in the content
    missing_required: list[str] = []  # active "required" phrases not present


class ContentReviewReport(BaseModel):
    seo: SeoScore
    compliance: ComplianceCheck
    brand_voice_aligned: bool | None = None  # None when the AI judge didn't run
    issues: list[str] = []
    suggestions: list[str] = []
    ai_generated: bool  # False when the deterministic-only path answered
