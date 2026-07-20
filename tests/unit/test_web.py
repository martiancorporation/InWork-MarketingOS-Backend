"""Unit tests for the website scraper (SSRF guard + CSS extraction).

The URL-guard cases assert unsafe URLs are rejected *before* any network call,
so they run offline; the extraction cases operate on in-memory strings.
"""

from __future__ import annotations

import httpx
import pytest

from app.utils.web import (
    _extract_colors,
    _extract_fonts,
    _extract_meta,
    _get,
    candidate_urls,
    fetch_page,
    normalize_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",  # non-http scheme
        "file:///etc/passwd",  # non-http scheme
        "http://localhost:8000",  # loopback
        "http://127.0.0.1/admin",  # loopback IP
        "http://169.254.169.254/",  # link-local (cloud metadata)
        "not-a-url",  # inferred https, but no dot -> rejected offline
    ],
)
def test_unsafe_urls_are_rejected(url: str):
    assert fetch_page(url) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("acme.com", "https://acme.com"),  # bare domain -> https
        ("  acme.com/path  ", "https://acme.com/path"),  # trimmed
        ("http://acme.com", "http://acme.com"),  # explicit scheme kept
        ("HTTPS://Acme.com", "HTTPS://Acme.com"),  # scheme preserved as-is
        ("", None),  # empty
        ("not-a-url", None),  # inferred, no dot
        ("ftp://acme.com", None),  # unsupported scheme
    ],
)
def test_normalize_url(raw: str, expected: str | None):
    assert normalize_url(raw) == expected


def test_candidate_urls_toggles_www_and_falls_back_to_http():
    cands = candidate_urls("acme.com")
    assert cands[0] == "https://acme.com"
    assert "https://www.acme.com/" in cands  # www toggle
    assert any(c.startswith("http://acme.com") for c in cands)  # http fallback


def test_candidate_urls_empty_for_junk():
    assert candidate_urls("not-a-url") == []


def test_get_refuses_redirect_to_internal_host():
    """A public URL that 302-redirects to a link-local metadata address must
    not be followed — the redirect target is re-validated per hop."""

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "evil.example":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
            )
        raise AssertionError(f"followed redirect to internal host: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    with client:
        assert _get("https://evil.example/", timeout=1.0, client=client) is None


def test_get_follows_safe_redirect():
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(200, text="<html>ok</html>")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    with client:
        # example.com resolves publicly; both hops pass the guard.
        assert _get("https://example.com/start", timeout=1.0, client=client) == "<html>ok</html>"


def test_extract_meta_reads_theme_color_and_description():
    html = (
        '<meta name="theme-color" content="#0D6EFD">'
        '<meta property="og:description" content="A friendly brand.">'
    )
    meta = _extract_meta(html)
    assert meta["theme-color"] == "#0D6EFD"
    assert meta["og:description"] == "A friendly brand."


def test_extract_colors_dedupes_and_ignores_black_white():
    css = "a{color:#0D6EFD}b{color:#0d6efd;background:#FFFFFF}c{border:#000}d{fill:rgb(220,53,69)}"
    colors = _extract_colors(css)
    assert "#0D6EFD" in colors  # deduped, case-normalized
    assert "#DC3545" in colors  # rgb(...) converted to hex
    assert "#FFFFFF" not in colors and "#000" not in colors  # noise filtered


def test_extract_fonts_skips_generics_and_css_vars():
    css = "body{font-family:'Fustat', var(--bs-font-sans-serif), Arial, sans-serif}"
    fonts = _extract_fonts(css, html="")
    assert fonts == ["Fustat"]  # generic + var() references dropped


def test_extract_fonts_reads_google_fonts_link():
    html = '<link href="https://fonts.googleapis.com/css2?family=Inter+Tight&display=swap">'
    assert "Inter Tight" in _extract_fonts(css="", html=html)
