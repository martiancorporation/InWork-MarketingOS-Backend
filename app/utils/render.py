"""Headless-browser page rendering for brand extraction (Playwright).

One render captures everything the regex scraper can't:

- **text** — the DOM *after* JavaScript ran, so SPAs yield real content;
- **colors** — computed styles sampled from prominent elements (headers,
  buttons, links, hero blocks), weighted by visible size, so brand accents
  outrank utility grays;
- **fonts** — the font families actually rendered, including JS-injected ones;
- **theme-color / description** — declared brand color and meta description;
- **screenshot** — a viewport JPEG for the model's vision pass.

Reliability measures (this is the primary fetch path, so it works hard):
- a realistic desktop-Chrome fingerprint (UA, headers, locale, and a stealth
  init script that hides ``navigator.webdriver``) to clear common bot walls;
- a fast ``domcontentloaded`` wait, a short best-effort ``networkidle`` settle,
  then extraction — quick on normal sites, tolerant of slow ones;
- retries per URL plus a short list of candidate URLs (``www``/apex toggle,
  ``http`` fallback);
- challenge-page detection (Cloudflare "Just a moment", etc.) so a bot wall is
  treated as a miss and the next candidate / the httpx fallback is tried.

Best-effort by design: Playwright missing, browser not installed, or every
attempt failing returns ``None`` so callers fall back to ``app/utils/web.py``.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any, NamedTuple
from urllib.parse import urlparse

from app.utils.web import (
    _GENERIC_FONTS,
    _as_hex,
    _clean_str,
    _is_blocked_ip,
    _is_public_http_url,
    candidate_urls,
)

logger = logging.getLogger("app.utils.render")

_VIEWPORT = {"width": 1280, "height": 800}
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_EXTRA_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}
_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]
# Hide the most obvious automation tell before any page script runs.
_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

_NAV_ATTEMPTS = 2
_MAX_CANDIDATES = 3
_SETTLE_MS = 600
_NETWORKIDLE_MS = 2_500

# Markers of an interstitial bot/challenge page — extracting these is useless.
_BLOCK_MARKERS = (
    "just a moment",
    "attention required",
    "verifying you are human",
    "enable javascript and cookies",
    "checking your browser",
    "access denied",
)

# Runs in the page: pull text, weighted computed colors, rendered fonts, and meta.
_EXTRACT_JS = """
() => {
  const out = { text: "", colors: [], fonts: [], title: null, themeColor: null, description: null };
  out.text = ((document.body && document.body.innerText) || "")
    .replace(/\\s+/g, " ").trim();
  out.title = document.title || null;
  const meta = (key) => {
    const el = document.querySelector('meta[name="' + key + '"]')
      || document.querySelector('meta[property="' + key + '"]');
    return el ? el.getAttribute("content") : null;
  };
  out.themeColor = meta("theme-color");
  out.description = meta("og:description") || meta("description");

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
    theme_color: str | None = None
    description: str | None = None


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


def _is_blocked_target(url: str) -> bool:
    """Cheap, DNS-free SSRF check for in-browser requests/redirects.

    Blocks non-http(s) schemes and any request to a literal private/reserved IP
    (e.g. a redirect to ``169.254.169.254`` for cloud metadata) or a local
    hostname. Hostnames that need DNS are allowed here — the top-level navigation
    target is already validated by ``_is_public_http_url`` before launch.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme in ("data", "blob", "about"):
        return False  # inert, browser-internal — allow
    if scheme not in ("http", "https"):
        return True  # file:, ftp:, gopher:, etc.
    host = parsed.hostname or ""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return _is_blocked_ip(ipaddress.ip_address(host))
    except ValueError:
        return False  # not a literal IP — a hostname; allow


async def _install_ssrf_guard(context: Any) -> None:
    """Abort any browser request bound for a private/reserved address."""

    async def _guard(route: Any) -> None:
        if _is_blocked_target(route.request.url):
            logger.warning("Blocked SSRF-unsafe browser request: %s", route.request.url)
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", _guard)


def _looks_blocked(data: dict[str, Any]) -> bool:
    """True if the page is empty or a known bot/challenge interstitial."""
    text = str(data.get("text") or "")
    if not text.strip():
        return True
    blob = (str(data.get("title") or "") + " " + text[:400]).lower()
    return len(text) < 600 and any(marker in blob for marker in _BLOCK_MARKERS)


def _to_page(data: dict[str, Any], screenshot: bytes, max_chars: int) -> RenderedPage:
    return RenderedPage(
        text=str(data.get("text", ""))[:max_chars],
        colors=_rank_colors([c for c in data.get("colors", []) if isinstance(c, str)]),
        fonts=_filter_fonts([f for f in data.get("fonts", []) if isinstance(f, str)]),
        screenshot=screenshot,
        theme_color=_as_hex(data.get("themeColor")),
        description=_clean_str(data.get("description")),
    )


async def render_page(
    url: str, *, timeout_ms: int = 15_000, max_chars: int = 8000
) -> RenderedPage | None:
    """Render ``url`` in headless Chromium; ``None`` on any failure.

    Tries each safe candidate URL with retries; returns the first that yields a
    real (non-blocked, non-empty) page.
    """
    # SSRF-filter candidates *before* launching a browser, so unsafe/invalid
    # input fails fast without paying the Chromium startup cost.
    targets = [c for c in candidate_urls(url)[:_MAX_CANDIDATES] if _is_public_http_url(c)]
    if not targets:
        return None
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed — falling back to plain scrape.")
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
            try:
                context = await browser.new_context(
                    viewport=_VIEWPORT,
                    user_agent=_CHROME_UA,
                    locale="en-US",
                    timezone_id="America/New_York",
                    extra_http_headers=_EXTRA_HEADERS,
                )
                await context.add_init_script(_STEALTH_JS)
                await _install_ssrf_guard(context)
                page = await context.new_page()
                for target in targets:
                    result = await _render_one(page, target, timeout_ms, max_chars)
                    if result is not None:
                        return result
                return None
            finally:
                await browser.close()
    except Exception:
        logger.warning("Headless render failed for %s", url, exc_info=True)
        return None


async def _render_one(
    page: Any, target: str, timeout_ms: int, max_chars: int
) -> RenderedPage | None:
    """Navigate to one URL (with retries) and extract; ``None`` if unusable."""
    for attempt in range(_NAV_ATTEMPTS):
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception:
            logger.info("Navigation failed for %s (attempt %d)", target, attempt + 1)
            continue
        # Best-effort: let late XHR/fonts settle, but never block on a chatty site.
        try:
            await page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_MS)
        except Exception:
            pass
        await page.wait_for_timeout(_SETTLE_MS)

        data = await page.evaluate(_EXTRACT_JS)
        if _looks_blocked(data):
            logger.info("Blocked/empty page for %s; trying next option.", target)
            return None
        screenshot = await page.screenshot(type="jpeg", quality=70)
        return _to_page(data, screenshot, max_chars)
    return None
