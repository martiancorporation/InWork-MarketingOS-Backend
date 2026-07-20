"""Onboarding-readiness scoring.

Mirrors the frontend's weighted checklist so the score shown during onboarding
matches what the UI computed. Pure/synchronous — takes a loaded ``Client`` (with
its relationships) and returns a report; no DB access of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.models.client import Client
from app.models.enums import ContactSide, IntegrationStatus
from app.schemas.onboarding import ReadinessItem, ReadinessReport


@dataclass(frozen=True)
class _Check:
    key: str
    label: str
    weight: int
    passes: Callable[[Client], bool]


def _has_contact(client: Client, side: ContactSide) -> bool:
    return any(c.side == side and c.email for c in client.contacts)


_CHECKS: tuple[_Check, ...] = (
    _Check("brand-voice", "Brand voice defined", 10, lambda c: bool((c.brand_voice or "").strip())),
    _Check(
        "about",
        "About the brand",
        8,
        lambda c: bool((c.about_brand or c.brand_extracted or "").strip()),
    ),
    _Check("colors", "Brand colors added", 8, lambda c: len(c.brand_colors) > 0),
    _Check("logo", "Logo uploaded", 8, lambda c: bool(c.logo_url)),
    _Check("platforms", "Marketing platforms selected", 8, lambda c: len(c.platforms) > 0),
    _Check("markets", "Operating markets described", 8, lambda c: bool((c.markets or "").strip())),
    _Check("goals", "Client goals captured", 12, lambda c: bool((c.goals or "").strip())),
    _Check("compliance", "Compliance rules entered", 10, lambda c: len(c.compliance_entries) > 0),
    _Check(
        "contacts-client",
        "At least 1 client contact",
        8,
        lambda c: _has_contact(c, ContactSide.client),
    ),
    _Check(
        "contacts-inwork",
        "At least 1 InWork contact",
        8,
        lambda c: _has_contact(c, ContactSide.inwork),
    ),
    _Check(
        "integrations",
        "At least 1 integration connected",
        12,
        lambda c: any(i.status == IntegrationStatus.connected for i in c.integrations),
    ),
)


class ReadinessService:
    def report(self, client: Client) -> ReadinessReport:
        total_weight = sum(check.weight for check in _CHECKS)
        earned = 0
        completed: list[str] = []
        missing: list[ReadinessItem] = []
        for check in _CHECKS:
            if check.passes(client):
                earned += check.weight
                completed.append(check.label)
            else:
                missing.append(ReadinessItem(key=check.key, label=check.label, weight=check.weight))
        score = round(earned / total_weight * 100) if total_weight else 0
        return ReadinessReport(score=score, completed=completed, missing=missing)
