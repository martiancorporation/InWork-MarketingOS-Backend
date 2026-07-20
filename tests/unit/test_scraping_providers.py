"""ScrapingBee / Brave providers + their wiring into brand extraction.

No network: the provider clients are faked. Covers (1) ``parse_page`` reused by
the ScrapingBee path, (2) config gating on both clients, (3) ScrapingBee being
preferred in ``_collect`` when configured, (4) graceful fallback when it returns
nothing, and (5) Brave research folded into the analyzed text.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.ai.brand_extraction import BrandExtractionService
from app.integrations.brave import BraveClient
from app.integrations.scrapingbee import ScrapingBeeClient
from app.schemas.onboarding import BrandExtractionRequest
from app.utils.render import RenderedPage
from app.utils.web import parse_page

_HTML = """
<html><head>
<meta name="theme-color" content="#1A2B3C">
<meta name="description" content="Acme makes great widgets.">
<style>body{color:#1A2B3C;font-family:'Poppins',sans-serif;background:#FF5722;}</style>
</head><body><h1>Acme</h1><p>We build widgets for the world.</p></body></html>
"""


def _integrations(*, scrapingbee_key=None, brave_key=None) -> SimpleNamespace:
    return SimpleNamespace(
        scrapingbee_api_key=scrapingbee_key,
        scrapingbee_configured=bool(scrapingbee_key),
        brave_api_key=brave_key,
        brave_configured=bool(brave_key),
    )


# ---- parse_page (reused by the ScrapingBee path) --------------------------


def test_parse_page_extracts_colors_fonts_meta():
    page = parse_page(_HTML, "https://acme.com")
    assert "widgets" in page.text.lower()
    assert page.theme_color == "#1A2B3C"
    assert page.description == "Acme makes great widgets."
    assert "#FF5722" in page.colors  # from the <style> block
    assert "Poppins" in page.fonts
    assert "sans-serif" not in [f.lower() for f in page.fonts]  # generic dropped


def test_parse_page_skips_external_css_when_no_getter():
    html = '<html><head><link rel="stylesheet" href="/site.css"></head><body>x</body></html>'
    # get_css=None → no network; must not raise and must still return content.
    page = parse_page(html, "https://acme.com")
    assert page.text.strip() == "x"


# ---- config gating --------------------------------------------------------


def test_scrapingbee_unconfigured_returns_none():
    sb = ScrapingBeeClient(settings=_integrations())
    assert sb.is_configured is False
    assert sb.fetch_html("https://acme.com") is None


def test_brave_unconfigured_returns_empty():
    brave = BraveClient(settings=_integrations())
    assert brave.is_configured is False
    assert brave.search("acme") == []


# ---- _collect: ScrapingBee preferred + fallback ---------------------------


def test_collect_prefers_scrapingbee_when_configured(monkeypatch):
    async def fake_render(url, **kw):
        raise AssertionError("render_page must not run when ScrapingBee succeeds")

    monkeypatch.setattr("app.ai.brand_extraction.render_page", fake_render)
    sb = ScrapingBeeClient(settings=_integrations(scrapingbee_key="k"))
    monkeypatch.setattr(sb, "fetch_html", lambda url, **kw: _HTML)

    svc = BrandExtractionService(scrapingbee=sb)
    sig = asyncio.run(svc._collect("https://acme.com"))

    assert sig.source == "scrapingbee"
    assert sig.theme_color == "#1A2B3C"
    assert "#FF5722" in sig.colors
    assert "Poppins" in sig.fonts
    assert sig.screenshot is None


def test_collect_falls_back_when_scrapingbee_empty(monkeypatch):
    async def fake_render(url, **kw):
        return RenderedPage(text="rendered", colors=["#123456"], fonts=["Inter"], screenshot=b"img")

    monkeypatch.setattr("app.ai.brand_extraction.render_page", fake_render)
    sb = ScrapingBeeClient(settings=_integrations(scrapingbee_key="k"))
    monkeypatch.setattr(sb, "fetch_html", lambda url, **kw: None)  # blocked / empty

    svc = BrandExtractionService(scrapingbee=sb)
    sig = asyncio.run(svc._collect("https://acme.com"))

    assert sig.source == "render"
    assert sig.text == "rendered"


def test_collect_skips_scrapingbee_when_unconfigured(monkeypatch):
    called = {"sb": False}

    def fetch_html(url, **kw):
        called["sb"] = True
        return _HTML

    async def fake_render(url, **kw):
        return RenderedPage(text="rendered", colors=[], fonts=[], screenshot=b"img")

    monkeypatch.setattr("app.ai.brand_extraction.render_page", fake_render)
    sb = ScrapingBeeClient(settings=_integrations())  # no key
    monkeypatch.setattr(sb, "fetch_html", fetch_html)

    svc = BrandExtractionService(scrapingbee=sb)
    sig = asyncio.run(svc._collect("https://acme.com"))

    assert sig.source == "render"
    assert called["sb"] is False  # never attempted without a key


# ---- Brave research -------------------------------------------------------


def test_research_returns_snippets_when_configured(monkeypatch):
    brave = BraveClient(settings=_integrations(brave_key="k"))
    monkeypatch.setattr(
        brave,
        "search",
        lambda q, **kw: [
            {"title": "Acme Inc", "description": "A widget maker.", "url": "https://x"},
            {"title": "", "description": "", "url": ""},  # empty row dropped
        ],
    )
    svc = BrandExtractionService(brave=brave)
    research = asyncio.run(svc._research("https://acme.com"))

    assert "Web research about the brand:" in research
    assert "Acme Inc: A widget maker." in research
    assert research.count("\n- ") == 1  # the empty row was skipped


def test_research_empty_when_unconfigured():
    svc = BrandExtractionService(brave=BraveClient(settings=_integrations()))
    assert asyncio.run(svc._research("https://acme.com")) == ""


def test_extract_folds_research_into_analysis(monkeypatch):
    """End-to-end (no real network/model): research text reaches _analyze."""
    seen: dict = {}

    sb = ScrapingBeeClient(settings=_integrations(scrapingbee_key="k"))
    monkeypatch.setattr(sb, "fetch_html", lambda url, **kw: _HTML)
    brave = BraveClient(settings=_integrations(brave_key="k"))
    monkeypatch.setattr(
        brave, "search", lambda q, **kw: [{"title": "T", "description": "D", "url": "u"}]
    )

    svc = BrandExtractionService(scrapingbee=sb, brave=brave)
    monkeypatch.setattr(type(svc._client), "is_configured", property(lambda self: True))

    async def fake_analyze(
        website, text, colors, fonts, screenshot, context=None, *, media_type="image/jpeg"
    ):
        seen["text"] = text
        return {"summary": "ok", "colors": [], "fonts": [], "tone": "warm", "imagery": "x"}

    monkeypatch.setattr(svc, "_analyze", fake_analyze)

    result = asyncio.run(svc.extract(BrandExtractionRequest(website="https://acme.com")))

    assert result.ai_generated is True
    assert "Web research about the brand:" in seen["text"]
    assert "T: D" in seen["text"]
