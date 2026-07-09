"""Unit tests for the headless renderer's offline-safe parts.

The SSRF guard runs before any browser launch, and the color/font
post-processing is pure — all of this runs without Chromium or a network.
"""

from __future__ import annotations

import asyncio

import pytest

from app.utils.render import _filter_fonts, _rank_colors, render_page


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",          # non-http scheme
        "http://localhost:8000",      # loopback
        "http://169.254.169.254/",    # link-local (cloud metadata)
        "not-a-url",                  # no host
    ],
)
def test_unsafe_urls_are_rejected_before_browser_launch(url: str):
    assert asyncio.run(render_page(url)) is None


def test_rank_colors_puts_brand_accents_ahead_of_utility_grays():
    ranked = _rank_colors(["#E5E7EB", "#9CA3AF", "#0D6EFD", "#FFFFFF", "#000000"])
    assert ranked[0] == "#0D6EFD"                      # saturated accent first
    assert "#FFFFFF" not in ranked and "#000000" not in ranked  # extremes dropped
    assert ranked.index("#0D6EFD") < ranked.index("#9CA3AF")    # grays demoted


def test_filter_fonts_drops_generics_and_dedupes():
    fonts = _filter_fonts(["Fustat", "Arial", "sans-serif", "Fustat", "Inter Tight"])
    assert fonts == ["Fustat", "Inter Tight"]
