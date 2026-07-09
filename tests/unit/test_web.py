"""Unit tests for the website-fetch SSRF guard.

These assert the guard rejects unsafe URLs *before* any network call, so they
run offline.
"""

from __future__ import annotations

import pytest

from app.utils.web import _extract_colors, _extract_fonts, fetch_page


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",          # non-http scheme
        "file:///etc/passwd",         # non-http scheme
        "http://localhost:8000",      # loopback
        "http://127.0.0.1/admin",     # loopback IP
        "http://169.254.169.254/",    # link-local (cloud metadata)
        "not-a-url",                  # no host
    ],
)
def test_unsafe_urls_are_rejected(url: str):
    assert fetch_page(url) is None


def test_extract_colors_dedupes_and_ignores_black_white():
    css = "a{color:#0D6EFD}b{color:#0d6efd;background:#FFFFFF}c{border:#000}d{fill:rgb(220,53,69)}"
    colors = _extract_colors(css)
    assert "#0D6EFD" in colors          # deduped, case-normalized
    assert "#DC3545" in colors          # rgb(...) converted to hex
    assert "#FFFFFF" not in colors and "#000" not in colors  # noise filtered


def test_extract_fonts_skips_generics_and_css_vars():
    css = "body{font-family:'Fustat', var(--bs-font-sans-serif), Arial, sans-serif}"
    fonts = _extract_fonts(css, html="")
    assert fonts == ["Fustat"]  # generic + var() references dropped


def test_extract_fonts_reads_google_fonts_link():
    html = '<link href="https://fonts.googleapis.com/css2?family=Inter+Tight&display=swap">'
    assert "Inter Tight" in _extract_fonts(css="", html=html)
