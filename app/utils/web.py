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
import json
import re
import socket
from collections import Counter
from collections.abc import Callable
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

_JSONLD_BLOCK_RE = re.compile(
    r'<script[^>]+type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR_HREF_RE = re.compile(r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_ICON_LINK_RE = re.compile(
    r'<link[^>]+rel\s*=\s*["\']?(?:shortcut icon|icon|apple-touch-icon)["\']?[^>]*>',
    re.IGNORECASE,
)

# Defense-in-depth caps on the new identity-extraction surface — a pathological
# page (thousands of tiny script tags, or one huge malformed JSON-LD blob)
# must not turn a single onboarding scan into a CPU/memory sink.
_MAX_JSONLD_BLOCKS = 20
_MAX_JSONLD_BLOCK_CHARS = 20_000
_MAX_ANCHORS_SCANNED = 500

# Hostname suffix -> platform id, for turning a footer/header link into a
# labeled social profile. Order doesn't matter; matched via endswith.
_SOCIAL_DOMAINS: dict[str, str] = {
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "x.com": "x",
    "twitter.com": "x",
    "youtube.com": "youtube",
    "tiktok.com": "tiktok",
}


class SocialLink(NamedTuple):
    platform: str
    url: str


class IdentitySignals(NamedTuple):
    """Deterministic company-identity facts pulled from the same fetch as the
    brand colors/fonts — used by the onboarding "auto-fill" flow. Every field
    is ``None``/empty when not reliably found; nothing here is ever guessed."""

    org_name: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    location: str | None = None  # "City, Region" only — never a full street address
    emails: list[str] = []
    phones: list[str] = []
    social_links: list[SocialLink] = []

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
    "sans-serif",
    "serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "inherit",
    "initial",
    "unset",
    "-apple-system",
    "blinkmacsystemfont",
    "segoe ui",
    "roboto",
    "helvetica",
    "arial",
    "ui-sans-serif",
    "ui-serif",
    "ui-monospace",
    "sans",
    "none",
}
# Near-universal, non-distinguishing colors.
_IGNORED_COLORS = {"#FFFFFF", "#000000", "#FFF", "#000"}


class PageContent(NamedTuple):
    text: str
    colors: list[str]
    fonts: list[str]
    theme_color: str | None = None
    description: str | None = None
    identity: IdentitySignals | None = None


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
    # True only when the caller's raw input spelled out a scheme (vs. a bare
    # domain that normalize_url defaulted to https).
    explicit_scheme = bool(_SCHEME_RE.match((raw or "").strip()))

    def build(scheme: str, hostname: str) -> str:
        return urlunparse(
            (scheme, hostname + port, parsed.path or "/", parsed.params, parsed.query, "")
        )

    out = [base, build(parsed.scheme, alt_host)]
    # A cleartext fallback is only offered when https was our own default
    # guess for a bare-domain input (some small sites really are HTTP-only) —
    # never when the caller explicitly asked for https://, since silently
    # falling back to plaintext on a connection failure is exactly the shape
    # of an SSL-stripping/downgrade attack an on-path adversary could force.
    if parsed.scheme == "https" and not explicit_scheme:
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
    private/loopback/link-local address is rejected (basic SSRF guard). This is
    a fast pre-filter (used by the headless-render path, which can't easily pin
    a connection to a specific IP); ``_resolve_public_ip`` below is the version
    ``_get`` actually connects with, to close the DNS-rebinding TOCTOU between
    this check and the real connection.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    return _resolve_public_ip(parsed.hostname) is not None


def _resolve_public_ip(hostname: str) -> str | None:
    """Resolve ``hostname`` and return one address literal to connect to, or
    ``None`` if resolution fails or *any* returned address is private/reserved.

    Rejecting the whole hostname when only some of its addresses are private
    (rather than just skipping those) matters because DNS can round-robin
    across records — accepting the hostname on the strength of its public
    addresses alone would still let a request land on a private one later.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, ValueError):
        return None
    if not infos:
        return None
    addrs: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            return None
        addrs.append(ip)
    if not addrs:
        return None
    # Prefer an IPv4 literal (matches typical resolver/client ordering); any
    # validated address is equally safe to pin to.
    for ip in addrs:
        if ip.version == 4:
            return str(ip)
    return str(addrs[0])


# Follow at most this many redirects, re-validating the target of each hop.
_MAX_REDIRECTS = 5


def _get(url: str, *, timeout: float, client: httpx.Client) -> str | None:
    """Fetch ``url`` with SSRF-safe manual redirect handling.

    Auto-redirects are disabled so every hop (including cross-host 3xx to an
    internal address) is re-validated against the private-range guard before we
    connect. The connection itself is made to the resolved, validated IP
    literal (not the hostname) — ``Host``/SNI are still set to the real
    hostname so virtual-hosting and certificate verification work — which
    closes the DNS-rebinding gap a plain "check then let the HTTP client
    re-resolve" approach has: a low-TTL attacker-controlled name could
    otherwise return a public address to the check and a private one (e.g.
    the cloud metadata IP) to the actual connection moments later.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        ip = _resolve_public_ip(parsed.hostname)
        if ip is None:
            return None
        host_literal = f"[{ip}]" if ":" in ip else ip
        netloc = host_literal + (f":{parsed.port}" if parsed.port else "")
        pinned = urlunparse(
            (parsed.scheme, netloc, parsed.path or "/", parsed.params, parsed.query, "")
        )
        try:
            resp = client.get(
                pinned,
                timeout=timeout,
                follow_redirects=False,
                headers={"Host": parsed.hostname},
                extensions={"sni_hostname": parsed.hostname},
            )
        except httpx.HTTPError:
            return None
        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                return None
            current = urljoin(current, location)  # resolve against the real hostname
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


def _parse_json_ld_blocks(texts: list[str]) -> list[dict]:
    """Parse a handful of already-extracted ``<script type="application/ld+json">``
    bodies into JSON-LD nodes, flattening any ``@graph`` array.

    Best-effort: a malformed block (invalid JSON, or valid JSON that isn't an
    object) is skipped rather than aborting the whole page — one bad script tag
    must never lose the rest of a page's structured data. Capped on both count
    and per-block size so a pathological page can't turn this into a CPU/memory
    sink (see module docstring caps).
    """
    nodes: list[dict] = []
    for text in texts[:_MAX_JSONLD_BLOCKS]:
        try:
            parsed = json.loads(text[:_MAX_JSONLD_BLOCK_CHARS])
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph")
            if isinstance(graph, list):
                nodes.extend(n for n in graph if isinstance(n, dict))
            else:
                nodes.append(candidate)
    return nodes


def _extract_json_ld(html: str) -> list[dict]:
    """Regex-slice JSON-LD ``<script>`` bodies out of raw HTML, then parse them."""
    return _parse_json_ld_blocks(_JSONLD_BLOCK_RE.findall(html))


def _find_organization(nodes: list[dict]) -> dict | None:
    """First node whose ``@type`` is (or includes) ``Organization``/``LocalBusiness``."""
    for node in nodes:
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if any(isinstance(t, str) and t in ("Organization", "LocalBusiness") for t in types):
            return node
    return None


def _contact_points(org: dict) -> list[dict]:
    contact = org.get("contactPoint")
    if isinstance(contact, dict):
        return [contact]
    if isinstance(contact, list):
        return [c for c in contact if isinstance(c, dict)]
    return []


def _address_label(org: dict) -> str | None:
    """``"City, Region"`` from a JSON-LD ``PostalAddress`` — never a full street
    address; ``Client.location`` is a short "Headquarters" label, not a mailing
    address field."""
    address = org.get("address")
    if not isinstance(address, dict):
        return None
    locality = _clean_str(address.get("addressLocality"), limit=120)
    region = _clean_str(address.get("addressRegion"), limit=120)
    parts = [p for p in (locality, region) if p]
    return ", ".join(parts) if parts else None


def _social_link_from_url(url: str) -> SocialLink | None:
    if not isinstance(url, str):
        return None
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for suffix, platform in _SOCIAL_DOMAINS.items():
        if host == suffix or host.endswith("." + suffix):
            return SocialLink(platform=platform, url=url)
    return None


def _extract_favicon(html: str, base_url: str) -> str | None:
    """First declared icon link, else the conventional ``/favicon.ico`` — an
    unverified default (never fetched here), used only as a last-resort logo
    fallback since there's no dedicated favicon field in the onboarding UI."""
    match = _ICON_LINK_RE.search(html)
    if match:
        href = _HREF_RE.search(match.group(0))
        if href:
            return urljoin(base_url, href.group(1))
    return urljoin(base_url, "/favicon.ico")


def _social_links_from(json_ld_nodes: list[dict], anchor_hrefs: list[str]) -> list[SocialLink]:
    found: dict[str, SocialLink] = {}
    org = _find_organization(json_ld_nodes)
    same_as = org.get("sameAs") if org else None
    for url in same_as if isinstance(same_as, list) else []:
        link = _social_link_from_url(url)
        if link and link.platform not in found:
            found[link.platform] = link
    for href in anchor_hrefs[:_MAX_ANCHORS_SCANNED]:
        link = _social_link_from_url(href)
        if link and link.platform not in found:
            found[link.platform] = link
    return list(found.values())


def _merge_identity(
    *,
    json_ld_nodes: list[dict],
    anchor_hrefs: list[str],
    favicon_url: str | None,
    meta: dict[str, str],
) -> IdentitySignals:
    """Merge already-extracted JSON-LD/OG/anchor/favicon signals into one
    identity record — source-agnostic (shared by the httpx-scrape path, which
    slices these out of raw HTML, and the headless-render path, which
    collects them via in-page JS). Priority, most-specific first: JSON-LD
    ``Organization``/``LocalBusiness`` beats Open Graph beats nothing — never
    a guess.
    """
    org = _find_organization(json_ld_nodes) or {}

    name = (
        _clean_str(org.get("name"), limit=160)
        or _clean_str(meta.get("og:site_name"), limit=160)
        or _clean_str(meta.get("og:title"), limit=160)
    )
    logo = org.get("logo")
    logo_url = _clean_str(logo.get("url") if isinstance(logo, dict) else logo, limit=1024)
    # Deliberately no og:image fallback for logo: it's usually a hero/banner
    # image, not a logo — mislabeling a banner as "Company Logo" is worse than
    # leaving the field blank for the operator to fill in by hand.

    emails: list[str] = []
    phones: list[str] = []
    for point in _contact_points(org):
        email = _clean_str(point.get("email"), limit=255)
        phone = _clean_str(point.get("telephone"), limit=40)
        if email and email not in emails:
            emails.append(email)
        if phone and phone not in phones:
            phones.append(phone)

    return IdentitySignals(
        org_name=name,
        logo_url=logo_url or favicon_url,
        favicon_url=favicon_url,
        location=_address_label(org),
        emails=emails,
        phones=phones,
        social_links=_social_links_from(json_ld_nodes, anchor_hrefs),
    )


def _build_identity(html: str, base_url: str, meta: dict[str, str]) -> IdentitySignals:
    """HTML-based entry point for ``_merge_identity`` — slices JSON-LD/anchor
    hrefs/favicon out of raw HTML (the httpx-scrape and ScrapingBee paths)."""
    return _merge_identity(
        json_ld_nodes=_extract_json_ld(html),
        anchor_hrefs=_ANCHOR_HREF_RE.findall(html),
        favicon_url=_extract_favicon(html, base_url),
        meta=meta,
    )


def parse_page(
    html: str,
    base_url: str,
    *,
    get_css: Callable[[str], str | None] | None = None,
    max_chars: int = 8000,
    max_css: int = 3,
) -> PageContent:
    """Turn already-fetched HTML into brand signals (text + colors/fonts/meta).

    Split out of ``fetch_page`` so an alternate fetcher (e.g. the ScrapingBee
    proxy) can reuse the exact same extraction. ``get_css`` optionally fetches a
    linked stylesheet by absolute URL (external CSS holds most brand colors); when
    ``None`` only inline/``<style>`` CSS is parsed — the callable is the sole way
    this function touches the network.
    """
    css = " ".join(_STYLE_BLOCK_RE.findall(html))
    css += " " + " ".join(_INLINE_STYLE_RE.findall(html))

    if get_css is not None:
        for i, link in enumerate(_LINK_CSS_RE.findall(html)):
            if i >= max_css:
                break
            href_match = _HREF_RE.search(link)
            if not href_match:
                continue
            sheet = get_css(urljoin(base_url, href_match.group(1)))
            if sheet:
                css += " " + sheet[: max_chars * 2]

    meta = _extract_meta(html)
    return PageContent(
        text=_clean_text(html, max_chars),
        colors=_extract_colors(css),
        fonts=_extract_fonts(css, html),
        theme_color=_as_hex(meta.get("theme-color")),
        description=_clean_str(meta.get("og:description") or meta.get("description")),
        identity=_build_identity(html, base_url, meta),
    )


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

        return parse_page(
            html,
            url,
            get_css=lambda u: _get(u, timeout=timeout, client=client),
            max_chars=max_chars,
            max_css=max_css,
        )
