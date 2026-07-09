"""AI-assisted brand extraction for client onboarding.

Uses Claude when configured; otherwise returns a deterministic, clearly-flagged
fallback so onboarding still works in local/dev without an API key. Either way
the endpoint contract (``BrandExtraction``) is identical, so wiring a real key
in production requires no code change.
"""

from __future__ import annotations

from app.ai.parsers import parse_json_object
from app.integrations.anthropic.client import AnthropicClient
from app.prompts.loader import load_prompt, render
from app.schemas.onboarding import BrandExtraction, BrandExtractionRequest


class BrandExtractionService:
    def __init__(self, client: AnthropicClient | None = None) -> None:
        self._client = client or AnthropicClient()

    async def extract(self, data: BrandExtractionRequest) -> BrandExtraction:
        if not self._client.is_configured:
            return self._fallback(data)

        system = load_prompt("brand_extraction/system.txt")
        user_prompt = render(
            load_prompt("brand_extraction/user_template.txt"),
            {"website": data.website or "(not provided)", "text": (data.text or "").strip()},
        )
        raw = await self._client.complete(system=system, prompt=user_prompt)
        payload = parse_json_object(raw)
        if payload is None:
            return self._fallback(data)

        return BrandExtraction(
            summary=str(payload.get("summary") or "").strip() or self._default_summary(data),
            colors=[c for c in payload.get("colors", []) if isinstance(c, str)],
            fonts=[f for f in payload.get("fonts", []) if isinstance(f, str)],
            tone=payload.get("tone"),
            imagery=payload.get("imagery"),
            ai_generated=True,
        )

    @staticmethod
    def _default_summary(data: BrandExtractionRequest) -> str:
        target = data.website or "the provided material"
        return f"Draft brand theme based on {target}. Review and refine before saving."

    def _fallback(self, data: BrandExtractionRequest) -> BrandExtraction:
        return BrandExtraction(
            summary=self._default_summary(data),
            colors=[],
            fonts=[],
            tone=None,
            imagery=None,
            ai_generated=False,
        )
