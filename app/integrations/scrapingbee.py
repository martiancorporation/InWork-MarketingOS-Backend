"""ScrapingBee fetch client — a proxied, JS-rendering page fetch.

Used by brand extraction as the preferred fetcher when configured: ScrapingBee
runs the request through rotating proxies with a real browser, so it gets past
the anti-bot / geo-IP blocks that a direct server-side scrape (or a headless
render from our own IP) runs into. Returns the rendered HTML, or ``None`` on any
failure so callers fall back to the headless render / httpx scrape.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.utils.web import normalize_url

logger = logging.getLogger("app.integrations.scrapingbee")

_BASE = "https://app.scrapingbee.com/api/v1/"


class ScrapingBeeClient:
    def __init__(self, settings=None) -> None:
        self._s = settings or get_settings().integrations

    @property
    def is_configured(self) -> bool:
        return self._s.scrapingbee_configured

    def fetch_html(
        self, url: str, *, render_js: bool = True, timeout: float = 40.0
    ) -> str | None:
        target = normalize_url(url)
        if not target or not self.is_configured:
            return None
        params = {
            "api_key": self._s.scrapingbee_api_key,
            "url": target,
            "render_js": "true" if render_js else "false",
            # Let ScrapingBee pick premium/geo proxies as needed to avoid blocks.
            "block_resources": "false",
        }
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(_BASE, params=params)
        except httpx.HTTPError as exc:
            logger.warning("ScrapingBee request failed for %s: %s", target, exc)
            return None
        if resp.status_code != 200 or not resp.text:
            logger.warning("ScrapingBee returned %s for %s", resp.status_code, target)
            return None
        return resp.text
