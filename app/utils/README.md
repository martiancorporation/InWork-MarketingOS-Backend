# app/utils/

Small, generic helpers usable across the app: `slug.py` (slug generation),
`download_link.py` (HMAC-signed upload links), `streaming.py` (SSE helpers),
and the two SSRF-guarded fetchers — `web.py` (httpx scrape) and `render.py`
(headless-Chromium render).

Most of these are dependency-free; the fetchers deliberately are not (httpx,
Playwright, and a settings read), because the SSRF guard has to live with the
code that makes the request. If a helper needs the database or orchestrates
business rules, it belongs in `services/` — not here.
