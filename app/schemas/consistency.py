"""Onboarding consistency-check schemas.

Matches the web ``runConsistencyCheck`` contract: a flat list of findings, each
with a ``level`` (ok / warn / error) and a human message. Surfaced at the review
step so an operator can catch contradictions between what they entered across
steps before the client is created (the "steel industry vs. gardens" case)."""

from __future__ import annotations

from pydantic import BaseModel

from app.models.enums import ConsistencyLevel


class ConsistencyFinding(BaseModel):
    level: ConsistencyLevel
    message: str
    step: str | None = None  # optional onboarding step the finding relates to


class ConsistencyReport(BaseModel):
    findings: list[ConsistencyFinding]
    has_blocking: bool  # true if any finding is an ``error``
    ai_generated: bool
