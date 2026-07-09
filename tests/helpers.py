"""Test helpers — payload builders shared across test modules."""

from __future__ import annotations

from typing import Any


def onboarding_payload(name: str = "Acme Co.", **overrides: Any) -> dict[str, Any]:
    """A valid client-onboarding request body; override any field via kwargs."""
    payload: dict[str, Any] = {
        "name": name,
        "business_type": "DTC E-commerce",
        "industry": "Home & Garden",
        "website": "https://acme.com",
        "language": "English (US)",
        "location": "Austin, TX",
        "markets": "Entire US, focus on TX/FL/CA metros",
        "brand": {
            "brand_voice": "Friendly, witty, never corporate.",
            "about_brand": "Joyful home goods.",
            "colors": [{"hex": "#0EA5E9", "label": "Primary"}, {"hex": "#1E3A8A"}],
            "fonts": ["Inter", "Source Sans Pro"],
            "logo_url": "https://acme.com/logo.svg",
        },
        "platforms": ["meta", "google-ads", "google-lsa", "seo"],
        "goals": "Q1 brand presence; Q2 lead-gen; Q3 e-commerce. Build momentum steadily.",
        "compliance": {"feed": "Never say 'cheap' or 'guaranteed'. Always include 'Made in USA'."},
        "client_contacts": [{"name": "Jane Cooper", "role": "CMO", "email": "jane@acme.com"}],
        "inwork_contacts": [{"name": "Alex Rivera", "role": "Strategist", "email": "alex@inwork.com"}],
        "documents": [],
    }
    payload.update(overrides)
    return payload
