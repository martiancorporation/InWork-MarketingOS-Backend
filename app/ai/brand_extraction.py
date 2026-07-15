"""AI-assisted brand extraction for client onboarding.

One-pass flow: signals are collected once (``_collect``) — preferring a headless
browser render (``app/utils/render.py``: post-JS text, computed colors/fonts,
declared theme-color/description, and a screenshot), falling back to a plain
httpx scrape (``app/utils/web.py``) when no browser is available. A single Claude
*vision* call then looks at the screenshot + text and writes summary/tone/imagery.

Graceful degradation, in order:
1. Headless render unavailable/failed → plain httpx scrape supplies text +
   colors/fonts/meta; the model call becomes text-only.
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

from anyio import to_thread

from app.ai.parsers import parse_json_object
from app.ai.usage import AiUsageContext
from app.integrations.anthropic.client import AnthropicClient
from app.prompts.loader import load_prompt, render
from app.schemas.onboarding import BrandExtraction, BrandExtractionRequest
from app.utils.render import render_page
from app.utils.web import fetch_page

logger = logging.getLogger("app.ai.brand_extraction")

_MODEL_ATTEMPTS = 2


@dataclass
class _Signals:
    """Everything one collection pass gathered, from whichever source succeeded."""

    text: str
    colors: list[str]
    fonts: list[str]
    screenshot: bytes | None
    theme_color: str | None
    description: str | None
    source: str  # "render" | "scrape" | "none"


class BrandExtractionService:
    def __init__(self, client: AnthropicClient | None = None) -> None:
        self._client = client or AnthropicClient()

    async def extract(
        self, data: BrandExtractionRequest, context: AiUsageContext | None = None
    ) -> BrandExtraction:
        sig = await self._collect(data.website)
        # Declared theme-color leads, then measured colors — both beat any guess.
        colors = _dedupe(([sig.theme_color] if sig.theme_color else []) + sig.colors)

        if sig.source == "none":
            logger.warning("Brand extraction fetched nothing for %s", data.website)
        if not self._client.is_configured:
            return self._fallback(data, colors, sig.fonts, sig.description)

        payload = await self._analyze(
            data.website, sig.text, colors, sig.fonts, sig.screenshot, context
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
        """Gather page signals once: headless render first, httpx scrape second."""
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

    async def _analyze(
        self,
        website: str,
        text: str,
        colors: list[str],
        fonts: list[str],
        screenshot: bytes | None,
        context: AiUsageContext | None = None,
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
                        system=system, prompt=prompt, image=screenshot, context=context
                    )
                else:
                    raw = await self._client.complete(
                        system=system, prompt=prompt, context=context
                    )
            except Exception:  # transient API error — retry once, then degrade
                logger.warning(
                    "Brand analysis attempt %d failed for %s", attempt + 1, website,
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
        return f"Draft brand theme based on {data.website}. Review and refine before saving."

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
