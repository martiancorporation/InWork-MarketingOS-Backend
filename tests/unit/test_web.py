"""Unit tests for the website scraper (SSRF guard + CSS extraction).

The URL-guard cases assert unsafe URLs are rejected *before* any network call,
so they run offline; the extraction cases operate on in-memory strings.
"""

from __future__ import annotations

import socket

import httpx
import pytest

from app.utils.web import (
    _extract_colors,
    _extract_favicon,
    _extract_fonts,
    _extract_meta,
    _get,
    _parse_json_ld_blocks,
    _resolve_public_ip,
    _social_link_from_url,
    candidate_urls,
    fetch_page,
    normalize_url,
    parse_page,
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


def test_candidate_urls_no_http_fallback_when_https_explicit():
    """An explicit https:// input keeps its security intent — no silent
    cleartext fallback (that would be an SSL-stripping shape)."""
    cands = candidate_urls("https://acme.com")
    assert not any(c.startswith("http://") for c in cands)


def test_candidate_urls_still_falls_back_for_bare_domain():
    """A bare-domain input only ever got https as our own default guess, so a
    genuinely HTTP-only site should still be reachable."""
    cands = candidate_urls("acme.com")
    assert any(c.startswith("http://") for c in cands)


def test_get_connects_to_the_resolved_ip_not_the_hostname(monkeypatch):
    """The connection is pinned to a validated IP literal (DNS-rebinding
    guard) — the Host header still carries the real hostname so the mock
    handler (and, in production, TLS SNI/cert checks) sees the right name."""
    # A real public IP so it passes the private/reserved-range guard — the
    # MockTransport below intercepts before any real socket is opened, so
    # nothing is actually contacted.
    fake_ip = "8.8.8.8"

    def _fake_getaddrinfo(host, *args, **kwargs):
        assert host == "pin-me.example"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (fake_ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    assert _resolve_public_ip("pin-me.example") == fake_ip

    seen = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["header"] = request.headers.get("host")
        return httpx.Response(200, text="ok")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    with client:
        assert _get("https://pin-me.example/", timeout=1.0, client=client) == "ok"

    assert seen["host"] == fake_ip
    assert seen["header"] == "pin-me.example"


def test_get_refuses_redirect_to_internal_host():
    """A public URL that 302-redirects to a link-local metadata address must
    not be followed — the redirect target is re-validated per hop.

    The connection is pinned to the resolved IP literal (DNS-rebinding guard),
    so the mock handler distinguishes hops by the ``Host`` header (still the
    real hostname) rather than ``request.url.host`` (now an IP literal).
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("host") == "evil.example":
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


# ---- identity extraction (JSON-LD / OG / favicon / social links) ----


def test_json_ld_organization_yields_full_identity():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "Organization", "name": "Acme Corporation",
     "logo": "https://acme.com/logo.png",
     "sameAs": ["https://www.instagram.com/acmeco"],
     "address": {"addressLocality": "Austin", "addressRegion": "TX"},
     "contactPoint": {"email": "hello@acme.com", "telephone": "+1-512-555-0100"}}
    </script>
    </head><body><a href="https://facebook.com/acmeco">FB</a></body></html>
    """
    page = parse_page(html, "https://acme.com/")
    identity = page.identity
    assert identity.org_name == "Acme Corporation"
    assert identity.logo_url == "https://acme.com/logo.png"
    assert identity.location == "Austin, TX"
    assert identity.emails == ["hello@acme.com"]
    assert identity.phones == ["+1-512-555-0100"]
    platforms = {link.platform for link in identity.social_links}
    assert platforms == {"instagram", "facebook"}


def test_json_ld_multiple_blocks_and_graph_nesting():
    html = """
    <script type="application/ld+json">{"@type": "WebSite", "name": "not this"}</script>
    <script type="application/ld+json">
      {"@graph": [{"@type": "BreadcrumbList"}, {"@type": "Organization", "name": "Graph Co"}]}
    </script>
    """
    nodes = _parse_json_ld_blocks(
        [
            '{"@type": "WebSite", "name": "not this"}',
            '{"@graph": [{"@type": "BreadcrumbList"}, {"@type": "Organization", "name": "Graph Co"}]}',
        ]
    )
    types = [n.get("@type") for n in nodes]
    assert "Organization" in types
    page = parse_page(html, "https://graph.example/")
    assert page.identity.org_name == "Graph Co"


def test_json_ld_malformed_block_does_not_kill_the_others():
    blocks = ["{not valid json", '{"@type": "Organization", "name": "Good Co"}', "[1, 2, 3]"]
    nodes = _parse_json_ld_blocks(blocks)
    assert len(nodes) == 1
    assert nodes[0]["name"] == "Good Co"


def test_json_ld_contact_point_as_array():
    blocks = [
        '{"@type": "Organization", "name": "Multi", '
        '"contactPoint": [{"email": "a@x.com"}, {"telephone": "555-0100", "email": "b@x.com"}]}'
    ]
    nodes = _parse_json_ld_blocks(blocks)
    page = parse_page(
        f'<script type="application/ld+json">{blocks[0]}</script>', "https://x.example/"
    )
    assert page.identity.emails == ["a@x.com", "b@x.com"]
    assert page.identity.phones == ["555-0100"]
    assert nodes  # sanity: at least parsed


def test_json_ld_block_count_and_size_are_capped():
    # 25 tiny blocks — only the first _MAX_JSONLD_BLOCKS (20) are parsed.
    texts = [f'{{"@type": "Organization", "name": "n{i}"}}' for i in range(25)]
    nodes = _parse_json_ld_blocks(texts)
    assert len(nodes) == 20

    # A single oversized block is truncated before json.loads, so it fails to
    # parse cleanly rather than being fed whole to the JSON parser.
    huge = '{"@type": "Organization", "name": "' + ("x" * 30_000) + '"}'
    assert _parse_json_ld_blocks([huge]) == []


def test_favicon_prefers_declared_link_else_default_path():
    html = '<link rel="icon" href="/assets/favicon.png">'
    assert _extract_favicon(html, "https://acme.com/") == "https://acme.com/assets/favicon.png"
    assert _extract_favicon("<html></html>", "https://acme.com/") == "https://acme.com/favicon.ico"


@pytest.mark.parametrize(
    "url,expected_platform",
    [
        ("https://www.instagram.com/acme", "instagram"),
        ("https://facebook.com/acme", "facebook"),
        ("https://linkedin.com/company/acme", "linkedin"),
        ("https://twitter.com/acme", "x"),
        ("https://x.com/acme", "x"),
        ("https://youtube.com/@acme", "youtube"),
        ("https://tiktok.com/@acme", "tiktok"),
    ],
)
def test_social_link_known_domains(url: str, expected_platform: str):
    link = _social_link_from_url(url)
    assert link is not None
    assert link.platform == expected_platform


def test_social_link_unknown_domain_is_ignored():
    assert _social_link_from_url("https://acme.com/about") is None


def test_identity_absent_when_page_has_no_signals():
    page = parse_page("<html><body>Hello</body></html>", "https://bare.example/")
    identity = page.identity
    assert identity.org_name is None
    assert identity.emails == []
    assert identity.social_links == []
    # Favicon still defaults, used only as a last-resort logo fallback.
    assert identity.logo_url == "https://bare.example/favicon.ico"
