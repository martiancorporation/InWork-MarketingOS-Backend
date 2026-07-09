"""Fetch a web page and pull out brand signals for AI onboarding.

Returns the visible text (for the model to summarize) plus **deterministically
extracted** colors and fonts scraped from inline styles, ``<style>`` blocks, up
to a few linked stylesheets, and Google-Fonts links. Colors/fonts are far more
reliable to pull from CSS directly than to ask the model to guess.

Dependency-light (``httpx`` + regex), best-effort: any failure yields ``None``.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections import Counter
from typing import NamedTuple
from urllib.parse import urljoin, urlparse

import httpx

_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
_INLINE_STYLE_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
_LINK_CSS_RE = re.compile(r'<link[^>]+rel=["\']?stylesheet["\']?[^>]*>', re.IGNORECASE)
_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_GOOGLE_FONT_RE = re.compile(r"fonts\.googleapis\.com/css2?\?([^\"'>]+)", re.IGNORECASE)
_FAMILY_QS_RE = re.compile(r"family=([^&:]+)", re.IGNORECASE)

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
_RGB_RE = re.compile(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", re.IGNORECASE)
_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}{]+)", re.IGNORECASE)
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Fonts that aren't real brand faces.
_GENERIC_FONTS = {
    "sans-serif", "serif", "monospace", "cursive", "fantasy", "system-ui",
    "inherit", "initial", "unset", "-apple-system", "blinkmacsystemfont",
    "segoe ui", "roboto", "helvetica", "arial", "ui-sans-serif", "ui-serif",
    "ui-monospace", "sans", "none",
}
# Near-universal, non-distinguishing colors.
_IGNORED_COLORS = {"#FFFFFF", "#000000", "#FFF", "#000"}


class PageContent(NamedTuple):
    text: str
    colors: list[str]
    fonts: list[str]


def _is_public_http_url(url: str) -> bool:
    """Only http(s) with a non-loopback/private/link-local host (basic SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        for info in socket.getaddrinfo(parsed.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except (socket.gaierror, ValueError):
        return False
    return True


def _get(url: str, *, timeout: float, client: httpx.Client) -> str | None:
    if not _is_public_http_url(url):
        return None
    try:
        resp = client.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPError:
        return None


def _clean_text(html: str, max_chars: int) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _ANY_TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()[:max_chars]


def _normalize_hex(value: str) -> str:
    v = value.upper()
    if len(v) == 4:  # #ABC -> #AABBCC
        v = "#" + "".join(ch * 2 for ch in v[1:])
    return v


def _extract_colors(css: str) -> list[str]:
    counter: Counter[str] = Counter()
    for m in _HEX_RE.findall(css):
        counter[_normalize_hex(m)] += 1
    for r, g, b in _RGB_RE.findall(css):
        try:
            counter["#" + "".join(f"{int(c):02X}" for c in (r, g, b))] += 1
        except ValueError:
            continue
    ranked = [c for c, _ in counter.most_common() if c not in _IGNORED_COLORS]
    return ranked[:6]


def _extract_fonts(css: str, html: str) -> list[str]:
    fonts: list[str] = []

    def add(name: str) -> None:
        cleaned = name.strip().strip("\"'").strip()
        low = cleaned.lower()
        if not cleaned or low in _GENERIC_FONTS or cleaned in fonts:
            return
        if low.startswith("var(") or cleaned.startswith("--"):  # CSS variable reference, not a font
            return
        fonts.append(cleaned)

    for decl in _FONT_FAMILY_RE.findall(css):
        first = decl.split(",")[0]
        add(first)
    for qs in _GOOGLE_FONT_RE.findall(html):
        for fam in _FAMILY_QS_RE.findall(qs):
            add(fam.replace("+", " "))
    return fonts[:6]


def fetch_page(url: str, *, timeout: float = 8.0, max_chars: int = 8000, max_css: int = 3) -> PageContent | None:
    """Fetch ``url`` and return its text + extracted brand colors/fonts."""
    with httpx.Client(
        headers={"User-Agent": "InWork-MarketingOS/1.0 (brand-extraction)"}
    ) as client:
        html = _get(url, timeout=timeout, client=client)
        if html is None:
            return None

        css = " ".join(_STYLE_BLOCK_RE.findall(html))
        css += " " + " ".join(_INLINE_STYLE_RE.findall(html))

        # Follow up to a few linked stylesheets (external CSS holds most colors).
        for i, link in enumerate(_LINK_CSS_RE.findall(html)):
            if i >= max_css:
                break
            href_match = _HREF_RE.search(link)
            if not href_match:
                continue
            sheet = _get(urljoin(url, href_match.group(1)), timeout=timeout, client=client)
            if sheet:
                css += " " + sheet[:max_chars * 2]

    return PageContent(
        text=_clean_text(html, max_chars),
        colors=_extract_colors(css),
        fonts=_extract_fonts(css, html),
    )
