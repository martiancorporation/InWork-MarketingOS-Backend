"""Fetch a web page and pull out brand signals for AI onboarding.

Returns the visible text (for the model to summarize) plus **deterministically
extracted** colors and fonts scraped from inline styles, ``<style>`` blocks, up
to a few linked stylesheets, and Google-Fonts links, plus a declared
``theme-color`` and the page's meta description. Colors/fonts are far more
reliable to pull from CSS directly than to ask the model to guess.

This is the *fallback* path used when the headless browser (``utils/render.py``)
is unavailable. It is deliberately dependency-light (``httpx`` + regex) and
best-effort: any failure yields ``None``. To survive as many sites as possible
it (1) normalizes bare-domain input into a real URL, (2) tries a short list of
candidate URLs (``www``/apex toggle, ``https``→``http`` fallback), and (3) sends
a realistic browser ``User-Agent``/headers so plain WAFs don't reject it.

The URL helpers (``normalize_url``, ``candidate_urls``, ``_is_public_http_url``)
are shared with the headless renderer.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections import Counter
from typing import NamedTuple
from urllib.parse import urljoin, urlparse, urlunparse

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

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-zA-Z:_-]+)\s*=\s*(["\'])(.*?)\2', re.DOTALL)
_HEX_FULL_RE = re.compile(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")

# A realistic desktop-Chrome fingerprint. A bespoke bot UA gets 403'd by many
# WAFs; presenting as a normal browser clears the low bar most sites set.
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_BROWSER_HEADERS = {
    "User-Agent": _CHROME_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
    "image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# Cap how many URL variants we probe so a dead host fails fast.
_MAX_CANDIDATES = 3

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
    theme_color: str | None = None
    description: str | None = None


def normalize_url(raw: str) -> str | None:
    """Turn user input into a usable http(s) URL, or ``None`` if unusable.

    Accepts a bare domain (``acme.com`` → ``https://acme.com``) — the single
    most common onboarding input that the old scheme-only guard rejected. When
    the scheme is inferred, the host must look like a domain (contain a dot) so
    obvious junk (``not-a-url``) is rejected offline without a DNS lookup.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    inferred = False
    if not _SCHEME_RE.match(raw):
        raw = "https://" + raw
        inferred = True
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if inferred and "." not in parsed.hostname:
        return None
    return raw


def candidate_urls(raw: str) -> list[str]:
    """Ordered URLs to try for one input: as given, ``www``/apex toggle, then an
    ``http`` fallback. Redirects are followed automatically, so this only covers
    connection/DNS-level failures a redirect can't."""
    base = normalize_url(raw)
    if not base:
        return []
    parsed = urlparse(base)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    alt_host = host[4:] if host.startswith("www.") else "www." + host

    def build(scheme: str, hostname: str) -> str:
        return urlunparse(
            (scheme, hostname + port, parsed.path or "/", parsed.params, parsed.query, "")
        )

    out = [base, build(parsed.scheme, alt_host)]
    if parsed.scheme == "https":
        out.append(build("http", host))

    seen: set[str] = set()
    result: list[str] = []
    for url in out:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address a server-side fetch must never reach."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 169.254.0.0/16 — cloud metadata lives here
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified  # 0.0.0.0 / ::
    )


def _is_public_http_url(url: str) -> bool:
    """Only http(s) whose host resolves exclusively to public addresses.

    Every resolved A/AAAA record is checked, so a name that returns *any*
    private/loopback/link-local address is rejected (basic SSRF guard).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
        if not infos:
            return False
        for info in infos:
            if _is_blocked_ip(ipaddress.ip_address(info[4][0])):
                return False
    except (socket.gaierror, ValueError):
        return False
    return True


# Follow at most this many redirects, re-validating the target of each hop.
_MAX_REDIRECTS = 5


def _get(url: str, *, timeout: float, client: httpx.Client) -> str | None:
    """Fetch ``url`` with SSRF-safe manual redirect handling.

    Auto-redirects are disabled so every hop (including cross-host 3xx to an
    internal address) is re-validated against the private-range guard before we
    connect — a plain ``follow_redirects=True`` would bypass the initial check.
    """
    for _ in range(_MAX_REDIRECTS + 1):
        if not _is_public_http_url(url):
            return None
        try:
            resp = client.get(url, timeout=timeout, follow_redirects=False)
        except httpx.HTTPError:
            return None
        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                return None
            url = urljoin(url, location)
            continue
        try:
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        return resp.text
    return None  # too many redirects


def _clean_text(html: str, max_chars: int) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _ANY_TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()[:max_chars]


def _as_hex(value: object) -> str | None:
    """Normalize a color string to ``#RRGGBB`` (expanding ``#RGB``), else ``None``."""
    if not isinstance(value, str):
        return None
    match = _HEX_FULL_RE.match(value.strip())
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return "#" + digits.upper()


def _clean_str(value: object, *, limit: int = 600) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:limit]
    return None


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


def _extract_meta(html: str) -> dict[str, str]:
    """Map ``name``/``property`` → ``content`` for every ``<meta>`` tag (first wins)."""
    out: dict[str, str] = {}
    for tag in _META_TAG_RE.findall(html):
        attrs = {m.group(1).lower(): m.group(3) for m in _ATTR_RE.finditer(tag)}
        key = attrs.get("name") or attrs.get("property")
        content = attrs.get("content")
        if key and content and key.lower() not in out:
            out[key.lower()] = content.strip()
    return out


def fetch_page(
    url: str, *, timeout: float = 10.0, max_chars: int = 8000, max_css: int = 3
) -> PageContent | None:
    """Fetch ``url`` and return its text + extracted brand colors/fonts/meta."""
    # Redirects are followed manually in ``_get`` so each hop is SSRF-checked.
    with httpx.Client(headers=_BROWSER_HEADERS, follow_redirects=False) as client:
        html: str | None = None
        for candidate in candidate_urls(url)[:_MAX_CANDIDATES]:
            html = _get(candidate, timeout=timeout, client=client)
            if html:
                url = candidate  # resolve relative stylesheet links against the hit
                break
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
                css += " " + sheet[: max_chars * 2]

    meta = _extract_meta(html)
    return PageContent(
        text=_clean_text(html, max_chars),
        colors=_extract_colors(css),
        fonts=_extract_fonts(css, html),
        theme_color=_as_hex(meta.get("theme-color")),
        description=_clean_str(meta.get("og:description") or meta.get("description")),
    )
