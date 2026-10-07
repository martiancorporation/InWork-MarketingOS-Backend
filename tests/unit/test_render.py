"""Unit tests for the headless renderer's offline-safe parts.

The SSRF guard runs before any browser launch, and the color/font
post-processing is pure — all of this runs without Chromium or a network.
"""

from __future__ import annotations

import asyncio

import pytest

from app.utils.render import _filter_fonts, _looks_blocked, _rank_colors, _to_page, render_page


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",  # non-http scheme
        "http://localhost:8000",  # loopback
        "http://169.254.169.254/",  # link-local (cloud metadata)
        "not-a-url",  # inferred https, no dot -> no safe candidate
    ],
)
def test_unsafe_urls_are_rejected_before_browser_launch(url: str):
    assert asyncio.run(render_page(url)) is None


def test_looks_blocked_flags_challenge_pages():
    assert _looks_blocked({"title": "Just a moment...", "text": "Checking your browser"})
    assert _looks_blocked({"text": "   "})  # empty
    # A real page that merely mentions a marker in passing is not flagged.
    assert not _looks_blocked({"title": "Acme", "text": "Welcome to Acme. " * 60})


def test_rank_colors_puts_brand_accents_ahead_of_utility_grays():
    ranked = _rank_colors(["#E5E7EB", "#9CA3AF", "#0D6EFD", "#FFFFFF", "#000000"])
    assert ranked[0] == "#0D6EFD"  # saturated accent first
    assert "#FFFFFF" not in ranked and "#000000" not in ranked  # extremes dropped
    assert ranked.index("#0D6EFD") < ranked.index("#9CA3AF")  # grays demoted


def test_filter_fonts_drops_generics_and_dedupes():
    fonts = _filter_fonts(["Fustat", "Arial", "sans-serif", "Fustat", "Inter Tight"])
    assert fonts == ["Fustat", "Inter Tight"]


def test_to_page_populates_identity_from_the_in_page_js_payload():
    """The Playwright path collects jsonLd/anchors/icons/siteName via JS
    (see _EXTRACT_JS) — this proves `_to_page` merges them through the same
    `_merge_identity` the httpx path uses, not a second, drifting copy."""
    data = {
        "text": "hi",
        "colors": [],
        "fonts": [],
        "themeColor": None,
        "description": None,
        "jsonLd": [
            '{"@type": "Organization", "name": "Render Co", '
            '"logo": "https://render.example/logo.png"}'
        ],
        "anchors": ["https://instagram.com/renderco"],
        "icons": ["/favicon.png"],
        "siteName": "Render Co Site",
        "ogTitle": None,
    }
    page = _to_page(data, b"", 8000, "https://render.example/")
    assert page.identity.org_name == "Render Co"
    assert page.identity.logo_url == "https://render.example/logo.png"
    assert page.identity.favicon_url == "https://render.example/favicon.png"
    assert page.identity.social_links[0].platform == "instagram"


def test_to_page_falls_back_to_og_site_name_and_default_favicon():
    data = {"text": "", "colors": [], "fonts": [], "siteName": "Fallback Co"}
    page = _to_page(data, b"", 8000, "https://fallback.example/")
    assert page.identity.org_name == "Fallback Co"
    assert page.identity.favicon_url == "https://fallback.example/favicon.ico"
