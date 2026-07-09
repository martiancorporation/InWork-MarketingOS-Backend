"""Headless-browser page rendering for brand extraction (Playwright).

One render captures everything the regex scraper can't:

- **text** — the DOM *after* JavaScript ran, so SPAs yield real content;
- **colors** — computed styles sampled from prominent elements (headers,
  buttons, links, hero blocks), weighted by visible size, so brand accents
  outrank utility grays;
- **fonts** — the font families actually rendered, including JS-injected ones;
- **screenshot** — a viewport JPEG for the model's vision pass.

Best-effort by design: Playwright missing, browser not installed, or any
navigation failure returns ``None`` so callers can fall back to the plain
httpx scrape in ``app/utils/web.py``.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from app.utils.web import _GENERIC_FONTS, _is_public_http_url

logger = logging.getLogger("app.utils.render")

_VIEWPORT = {"width": 1280, "height": 800}
_USER_AGENT = "InWork-MarketingOS/1.0 (brand-extraction)"

# Runs in the page: pull text, weighted computed colors, and rendered fonts.
_EXTRACT_JS = """
() => {
  const out = { text: "", colors: [], fonts: [] };
  out.text = ((document.body && document.body.innerText) || "")
    .replace(/\\s+/g, " ").trim();

  const colorWeights = new Map();
  const addColor = (value, weight) => {
    if (!value) return;
    const m = value.match(/rgba?\\(\\s*(\\d+)\\s*,\\s*(\\d+)\\s*,\\s*(\\d+)(?:\\s*,\\s*([\\d.]+))?\\)/);
    if (!m) return;
    if (m[4] !== undefined && parseFloat(m[4]) < 0.5) return;
    const hex = "#" + [m[1], m[2], m[3]]
      .map((v) => Number(v).toString(16).padStart(2, "0")).join("").toUpperCase();
    colorWeights.set(hex, (colorWeights.get(hex) || 0) + weight);
  };
  const prominent = document.querySelectorAll(
    'header, nav, footer, h1, h2, h3, a, button, [role="button"], ' +
    '[class*="btn"], [class*="hero"], [class*="primary"], [class*="brand"], [class*="accent"]'
  );
  for (const el of prominent) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) continue;
    const style = getComputedStyle(el);
    const weight = Math.min(Math.sqrt(rect.width * rect.height), 300);
    addColor(style.backgroundColor, weight * 2);  // backgrounds carry the brand
    addColor(style.color, weight);
    addColor(style.borderColor, weight / 2);
  }
  out.colors = [...colorWeights.entries()]
    .sort((a, b) => b[1] - a[1]).map(([c]) => c);

  const seen = new Set();
  for (const sel of ["h1", "h2", "h3", "button", "a", "p", "body"]) {
    const el = document.querySelector(sel);
    if (!el) continue;
    const fam = (getComputedStyle(el).fontFamily || "")
      .split(",")[0].trim().replace(/^["']|["']$/g, "");
    const low = fam.toLowerCase();
    if (fam && !seen.has(low)) { seen.add(low); out.fonts.push(fam); }
  }
  return out;
}
"""


class RenderedPage(NamedTuple):
    text: str
    colors: list[str]
    fonts: list[str]
    screenshot: bytes  # viewport JPEG


def _is_extreme(hex_color: str) -> bool:
    """Near-white / near-black — universal chrome, not brand."""
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return min(r, g, b) >= 245 or max(r, g, b) <= 16


def _is_gray(hex_color: str) -> bool:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return max(r, g, b) - min(r, g, b) < 20


def _rank_colors(hexes: list[str], limit: int = 6) -> list[str]:
    """Keep weighted order but put saturated (brand) colors ahead of grays."""
    usable = [c for c in hexes if len(c) == 7 and not _is_extreme(c)]
    saturated = [c for c in usable if not _is_gray(c)]
    grays = [c for c in usable if _is_gray(c)]
    return (saturated + grays)[:limit]


def _filter_fonts(fonts: list[str], limit: int = 6) -> list[str]:
    out: list[str] = []
    for fam in fonts:
        cleaned = fam.strip()
        if cleaned and cleaned.lower() not in _GENERIC_FONTS and cleaned not in out:
            out.append(cleaned)
    return out[:limit]


async def render_page(
    url: str, *, timeout_ms: int = 20_000, max_chars: int = 8000
) -> RenderedPage | None:
    """Render ``url`` in headless Chromium; ``None`` on any failure."""
    if not _is_public_http_url(url):
        return None
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed — falling back to plain scrape.")
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(
                    viewport=_VIEWPORT, user_agent=_USER_AGENT
                )
                try:
                    await page.goto(url, wait_until="load", timeout=timeout_ms)
                except Exception:
                    # Slow site — work with whatever has loaded so far.
                    logger.info("Load timeout for %s; extracting current state.", url)
                await page.wait_for_timeout(1_500)  # let late JS/fonts settle

                data = await page.evaluate(_EXTRACT_JS)
                screenshot = await page.screenshot(type="jpeg", quality=70)
            finally:
                await browser.close()
    except Exception:
        logger.warning("Headless render failed for %s", url, exc_info=True)
        return None

    return RenderedPage(
        text=str(data.get("text", ""))[:max_chars],
        colors=_rank_colors([c for c in data.get("colors", []) if isinstance(c, str)]),
        fonts=_filter_fonts([f for f in data.get("fonts", []) if isinstance(f, str)]),
        screenshot=screenshot,
    )
