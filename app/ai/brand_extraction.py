"""AI-assisted brand extraction for client onboarding.

The model summarizes the site's *text* (tone, positioning, imagery), while
colors and fonts are pulled **deterministically from the page's CSS** — the
model can't reliably guess hex codes or font names, and our prompt forbids
guessing. When Claude is not configured (no API key), a deterministic fallback
is returned; the extracted colors/fonts are still included.
"""

from __future__ import annotations

from anyio import to_thread

from app.ai.parsers import parse_json_object
from app.integrations.anthropic.client import AnthropicClient
from app.prompts.loader import load_prompt, render
from app.schemas.onboarding import BrandExtraction, BrandExtractionRequest
from app.utils.web import PageContent, fetch_page


class BrandExtractionService:
    def __init__(self, client: AnthropicClient | None = None) -> None:
        self._client = client or AnthropicClient()

    async def extract(self, data: BrandExtractionRequest) -> BrandExtraction:
        page = await self._fetch_page(data)
        colors = list(page.colors) if page else []
        fonts = list(page.fonts) if page else []

        if not self._client.is_configured:
            return self._fallback(data, colors, fonts)

        reference = self._reference_text(data, page)
        user_prompt = render(
            load_prompt("brand_extraction/user_template.txt"),
            {
                "website": data.website or "(not provided)",
                "text": reference or "(no readable reference content was available)",
            },
        )
        raw = await self._client.complete(
            system=load_prompt("brand_extraction/system.txt"), prompt=user_prompt
        )
        payload = parse_json_object(raw)
        if payload is None:
            return self._fallback(data, colors, fonts)

        model_colors = [c for c in payload.get("colors", []) if isinstance(c, str)]
        model_fonts = [f for f in payload.get("fonts", []) if isinstance(f, str)]
        return BrandExtraction(
            summary=str(payload.get("summary") or "").strip() or self._default_summary(data),
            # CSS-extracted values first (reliable), then anything the model added.
            colors=_dedupe(colors + model_colors)[:8],
            fonts=_dedupe(fonts + model_fonts)[:8],
            tone=_clean(payload.get("tone")),
            imagery=_clean(payload.get("imagery")),
            ai_generated=True,
        )

    async def _fetch_page(self, data: BrandExtractionRequest) -> PageContent | None:
        if not data.website:
            return None
        return await to_thread.run_sync(fetch_page, data.website)

    def _reference_text(self, data: BrandExtractionRequest, page: PageContent | None) -> str:
        parts: list[str] = []
        if data.text and data.text.strip():
            parts.append(data.text.strip())
        if page:
            if page.text:
                parts.append(f"Website content ({data.website}):\n{page.text}")
            if page.colors:
                parts.append("Detected brand colors: " + ", ".join(page.colors))
            if page.fonts:
                parts.append("Detected fonts: " + ", ".join(page.fonts))
        return "\n\n".join(parts)

    @staticmethod
    def _default_summary(data: BrandExtractionRequest) -> str:
        target = data.website or "the provided material"
        return f"Draft brand theme based on {target}. Review and refine before saving."

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
