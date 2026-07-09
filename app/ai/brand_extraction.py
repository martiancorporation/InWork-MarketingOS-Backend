"""AI-assisted brand extraction for client onboarding.

One-pass flow: the site is rendered in a headless browser
(``app/utils/render.py``), which yields everything at once — the post-JS DOM
text, computed brand **colors** and **fonts** (deterministic, sampled from
prominent elements), and a **screenshot**. A single Claude *vision* call then
looks at the screenshot + text and writes summary/tone/imagery.

Graceful degradation, in order:
1. Headless render unavailable/failed → plain httpx scrape (``utils/web.py``)
   supplies text + CSS colors/fonts; the model call becomes text-only.
2. Model call fails or returns unparseable JSON → deterministic response with
   the rendered/scraped colors+fonts (``ai_generated`` False).
3. No API key at all → same deterministic response, no model call.

Merge rules: colors/fonts prefer the rendered/scraped values (measured, never
guessed), then anything the model itself observed on the page.
"""

from __future__ import annotations

import logging
from typing import Any

from anyio import to_thread

from app.ai.parsers import parse_json_object
from app.integrations.anthropic.client import AnthropicClient
from app.prompts.loader import load_prompt, render
from app.schemas.onboarding import BrandExtraction, BrandExtractionRequest
from app.utils.render import render_page
from app.utils.web import fetch_page

logger = logging.getLogger("app.ai.brand_extraction")


class BrandExtractionService:
    def __init__(self, client: AnthropicClient | None = None) -> None:
        self._client = client or AnthropicClient()

    async def extract(self, data: BrandExtractionRequest) -> BrandExtraction:
        # One render captures text + colors + fonts + screenshot together.
        page = await render_page(data.website)
        screenshot: bytes | None = page.screenshot if page else None
        if page is None:
            # No browser / render failed — fall back to the plain scrape.
            static = await to_thread.run_sync(fetch_page, data.website)
            text = static.text if static else ""
            colors = list(static.colors) if static else []
            fonts = list(static.fonts) if static else []
        else:
            text, colors, fonts = page.text, list(page.colors), list(page.fonts)

        if not self._client.is_configured:
            return self._fallback(data, colors, fonts)

        payload = await self._analyze(data.website, text, colors, fonts, screenshot)
        if payload is None:
            return self._fallback(data, colors, fonts)

        model_colors = [c for c in payload.get("colors", []) if isinstance(c, str)]
        model_fonts = [f for f in payload.get("fonts", []) if isinstance(f, str)]
        return BrandExtraction(
            summary=str(payload.get("summary") or "").strip() or self._default_summary(data),
            # Measured values first (computed styles / CSS), then model-observed.
            colors=_dedupe(colors + model_colors)[:8],
            fonts=_dedupe(fonts + model_fonts)[:8],
            tone=_clean(payload.get("tone")),
            imagery=_clean(payload.get("imagery")),
            ai_generated=True,
        )

    async def _analyze(
        self,
        website: str,
        text: str,
        colors: list[str],
        fonts: list[str],
        screenshot: bytes | None,
    ) -> dict[str, Any] | None:
        """One model call: vision (screenshot + text) when we have a render,
        text-only otherwise. Returns ``None`` on any failure."""
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
        try:
            if screenshot is not None:
                raw = await self._client.complete_with_image(
                    system=system, prompt=prompt, image=screenshot
                )
            else:
                raw = await self._client.complete(system=system, prompt=prompt)
        except Exception:  # degrade to the deterministic fallback, never 500
            logger.warning("Brand analysis failed for %s", website, exc_info=True)
            return None
        return parse_json_object(raw)

    @staticmethod
    def _default_summary(data: BrandExtractionRequest) -> str:
        return f"Draft brand theme based on {data.website}. Review and refine before saving."

    def _fallback(
        self, data: BrandExtractionRequest, colors: list[str], fonts: list[str]
    ) -> BrandExtraction:
        return BrandExtraction(
            summary=self._default_summary(data),
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
