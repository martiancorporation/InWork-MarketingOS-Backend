"""AI-assisted brand extraction for client onboarding.

One-pass flow: signals are collected once (``_collect``). Fetch order, most
robust first:
0. **ScrapingBee** (when configured) — a proxied, JS-rendering fetch that gets
   past the anti-bot / geo-IP blocks a same-server render or scrape hits. Yields
   text + colors/fonts/meta (no screenshot → the model call is text-only).
1. Headless browser render (``app/utils/render.py``): post-JS text, computed
   colors/fonts, declared theme-color/description, and a screenshot for vision.
2. Plain httpx scrape (``app/utils/web.py``).

Optionally, when the **Brave Search API** is configured, a short web-research
snippet about the brand is folded into the text the model sees, so the summary
and tone reflect more than just the landing page.

A single Claude call then writes summary/tone/imagery — *vision* when a
screenshot is available (render path), text-only otherwise.

Graceful degradation, in order:
1. ScrapingBee unconfigured/failed → headless render; render unavailable → httpx
   scrape supplies text + colors/fonts/meta.
2. Model call fails or returns unparseable JSON (after one retry) → deterministic
   response with the measured colors/fonts (``ai_generated`` False), its summary
   seeded from the site's meta description when present.
3. No API key at all → same deterministic response, no model call.
4. Nothing could be fetched at all → deterministic response that says so.

Merge rules: colors lead with the site's *declared* ``theme-color``, then values
measured from computed styles/CSS, then anything the model itself observed —
measured always beats guessed, and nothing is fabricated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from anyio import to_thread

from app.ai.parsers import parse_json_object
from app.ai.usage import AiUsageContext
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.brave import BraveClient
from app.integrations.documents.extractor import extract_text
from app.integrations.scrapingbee import ScrapingBeeClient
from app.prompts.loader import load_prompt, render
from app.schemas.onboarding import BrandExtraction, BrandExtractionRequest
from app.utils.render import render_page
from app.utils.web import fetch_page, normalize_url, parse_page

logger = logging.getLogger("app.ai.brand_extraction")

_MODEL_ATTEMPTS = 2
_RESEARCH_RESULTS = 5
_MAX_DOC_CHARS = 8000  # cap document text fed to the model (parity with web scrape)


@dataclass
class DocumentInput:
    """A resolved uploaded document to extract a brand theme from.

    Either ``text`` (parsed from PDF/DOCX/etc.) or ``image`` bytes (a logo /
    brand-deck image → Claude vision) drives the extraction.
    """

    text: str = ""
    image: bytes | None = None
    media_type: str = "image/jpeg"
    filename: str = ""


@dataclass
class _Signals:
    """Everything one collection pass gathered, from whichever source succeeded."""

    text: str
    colors: list[str]
    fonts: list[str]
    screenshot: bytes | None
    theme_color: str | None
    description: str | None
    source: str  # "scrapingbee" | "render" | "scrape" | "none"


class BrandExtractionService:
    def __init__(
        self,
        client: AnthropicClient | None = None,
        *,
        scrapingbee: ScrapingBeeClient | None = None,
        brave: BraveClient | None = None,
    ) -> None:
        self._client = client or AnthropicClient()
        self._scrapingbee = scrapingbee or ScrapingBeeClient()
        self._brave = brave or BraveClient()

    @staticmethod
    def document_from_bytes(
        data: bytes, content_type: str | None, filename: str = ""
    ) -> DocumentInput:
        """Turn an uploaded file's bytes into a ``DocumentInput``.

        Images (a logo / brand-deck screenshot) go through Claude vision; every
        other type is parsed to text via the shared document extractor.
        """
        ctype = (content_type or "").split(";")[0].strip().lower()
        if ctype.startswith("image/"):
            return DocumentInput(image=data, media_type=ctype or "image/jpeg", filename=filename)
        result = extract_text(data, content_type, filename)
        return DocumentInput(text=(result.text or "")[:_MAX_DOC_CHARS], filename=filename)

    async def extract(
        self,
        data: BrandExtractionRequest,
        context: AiUsageContext | None = None,
        *,
        document: DocumentInput | None = None,
    ) -> BrandExtraction:
        if document is not None:
            # Brand from an uploaded document: use its parsed text and/or image
            # (vision) directly — no web fetch.
            sig = _Signals(
                text=document.text,
                colors=[],
                fonts=[],
                screenshot=document.image,
                theme_color=None,
                description=None,
                source="document",
            )
            media_type = document.media_type
            label = document.filename or "the uploaded document"
        else:
            sig = await self._collect(data.website)
            media_type = "image/jpeg"
            label = data.website or "the website"

        # Declared theme-color leads, then measured colors — both beat any guess.
        colors = _dedupe(([sig.theme_color] if sig.theme_color else []) + sig.colors)

        if sig.source == "none":
            logger.warning("Brand extraction fetched nothing for %s", data.website)
        if not self._client.is_configured:
            return self._fallback(data, colors, sig.fonts, sig.description)

        # Optional web research (Brave) enriches the text — website path only.
        research = await self._research(data.website) if data.website else ""
        text = f"{sig.text}\n\n{research}".strip() if research else sig.text

        payload = await self._analyze(
            label, text, colors, sig.fonts, sig.screenshot, context, media_type=media_type
        )
        if payload is None:
            return self._fallback(data, colors, sig.fonts, sig.description)

        model_colors = [c for c in payload.get("colors", []) if isinstance(c, str)]
        model_fonts = [f for f in payload.get("fonts", []) if isinstance(f, str)]
        return BrandExtraction(
            summary=str(payload.get("summary") or "").strip() or self._default_summary(data),
            colors=_dedupe(colors + model_colors)[:8],
            fonts=_dedupe(sig.fonts + model_fonts)[:8],
            tone=_clean(payload.get("tone")),
            imagery=_clean(payload.get("imagery")),
            ai_generated=True,
        )

    async def _collect(self, website: str) -> _Signals:
        """Gather page signals once. ScrapingBee (proxied, beats blocks) first
        when configured, then a headless render, then a plain httpx scrape."""
        if self._scrapingbee.is_configured:
            html = await to_thread.run_sync(self._scrapingbee.fetch_html, website)
            if html:
                # No CSS-following here: one ScrapingBee call keeps credits low;
                # inline/<style> CSS + meta still yield colors/fonts/description.
                page = parse_page(html, normalize_url(website) or website)
                return _Signals(
                    text=page.text,
                    colors=list(page.colors),
                    fonts=list(page.fonts),
                    screenshot=None,
                    theme_color=page.theme_color,
                    description=page.description,
                    source="scrapingbee",
                )
            logger.info("ScrapingBee returned nothing for %s; falling back", website)
        page = await render_page(website)
        if page is not None:
            return _Signals(
                text=page.text,
                colors=list(page.colors),
                fonts=list(page.fonts),
                screenshot=page.screenshot,
                theme_color=page.theme_color,
                description=page.description,
                source="render",
            )
        static = await to_thread.run_sync(fetch_page, website)
        if static is not None:
            return _Signals(
                text=static.text,
                colors=list(static.colors),
                fonts=list(static.fonts),
                screenshot=None,
                theme_color=static.theme_color,
                description=static.description,
                source="scrape",
            )
        return _Signals("", [], [], None, None, None, "none")

    async def _research(self, website: str) -> str:
        """Brave web-research snippets about the brand, or ``""`` when the Brave
        API is unconfigured / returns nothing. Never raises — best-effort enrichment."""
        if not self._brave.is_configured:
            return ""
        host = urlparse(normalize_url(website) or website).hostname or website
        query = f"{host} brand company about"
        results = await to_thread.run_sync(
            lambda: self._brave.search(query, count=_RESEARCH_RESULTS)
        )
        if not results:
            return ""
        lines = [
            f"- {r['title']}: {r['description']}"
            for r in results
            if r.get("title") or r.get("description")
        ]
        if not lines:
            return ""
        return "Web research about the brand:\n" + "\n".join(lines)

    async def _analyze(
        self,
        website: str,
        text: str,
        colors: list[str],
        fonts: list[str],
        screenshot: bytes | None,
        context: AiUsageContext | None = None,
        *,
        media_type: str = "image/jpeg",
    ) -> dict[str, Any] | None:
        """One model call (retried once): vision when we have a screenshot,
        text-only otherwise. Returns ``None`` on repeated failure."""
        reference_parts = []
        if text:
            reference_parts.append(f"Rendered page text ({website}):\n{text}")
        if colors:
            reference_parts.append("Detected brand colors: " + ", ".join(colors))
        if fonts:
            reference_parts.append("Detected fonts: " + ", ".join(fonts))
        prompt = render(
            load_prompt("brand_extraction/user_template.txt"),
            {
                "website": website,
                "text": "\n\n".join(reference_parts) or "(no readable content was captured)",
            },
        )
        system = load_prompt("brand_extraction/system.txt")

        for attempt in range(_MODEL_ATTEMPTS):
            try:
                if screenshot is not None:
                    raw = await self._client.complete_with_image(
                        system=system,
                        prompt=prompt,
                        image=screenshot,
                        media_type=media_type,
                        context=context,
                    )
                else:
                    raw = await self._client.complete(system=system, prompt=prompt, context=context)
            except Exception:  # transient API error — retry once, then degrade
                logger.warning(
                    "Brand analysis attempt %d failed for %s",
                    attempt + 1,
                    website,
                    exc_info=True,
                )
                continue
            payload = parse_json_object(raw)
            if payload is not None:
                return payload
            logger.info("Unparseable model output for %s (attempt %d)", website, attempt + 1)
        return None

    @staticmethod
    def _default_summary(data: BrandExtractionRequest) -> str:
        source = data.website or "the uploaded document"
        return f"Draft brand theme based on {source}. Review and refine before saving."

    def _fallback(
        self,
        data: BrandExtractionRequest,
        colors: list[str],
        fonts: list[str],
        description: str | None = None,
    ) -> BrandExtraction:
        summary = (description or "").strip() or self._default_summary(data)
        return BrandExtraction(
            summary=summary[:600],
            colors=colors[:8],
            fonts=fonts[:8],
            tone=None,
            imagery=None,
            ai_generated=False,
        )


def _clean(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out
