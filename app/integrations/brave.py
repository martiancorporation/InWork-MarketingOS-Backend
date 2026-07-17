"""Brave Search API client — brand research.

Given a brand/domain, returns top web results (title + snippet) so the brand
extractor can enrich its summary/tone with real information found online, not
just what's on the landing page. Optional: returns ``[]`` when unconfigured or
on any failure, so it never blocks extraction.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("app.integrations.brave")

_BASE = "https://api.search.brave.com/res/v1/web/search"


class BraveClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    @property
    def is_configured(self) -> bool:
        return self._s.brave_configured

    def search(self, query: str, *, count: int = 5, timeout: float = 15.0) -> list[dict]:
        if not self.is_configured or not query.strip():
            return []
        headers = {
            "X-Subscription-Token": self._s.brave_api_key or "",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(
                    _BASE, params={"q": query, "count": count}, headers=headers
                )
        except httpx.HTTPError as exc:
            logger.warning("Brave search failed for %r: %s", query, exc)
            return []
        if resp.status_code != 200:
            logger.warning("Brave search returned %s for %r", resp.status_code, query)
            return []
        try:
            results = (resp.json().get("web") or {}).get("results") or []
        except ValueError:
            return []
        out: list[dict] = []
        for item in results[:count]:
            out.append(
                {
                    "title": (item.get("title") or "").strip(),
                    "description": (item.get("description") or "").strip(),
                    "url": item.get("url") or "",
                }
            )
        return out
