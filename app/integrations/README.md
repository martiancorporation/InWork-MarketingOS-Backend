# app/integrations/

Thin clients that wrap **external** third-party APIs. Each provider gets its own
subpackage so credentials, request shapes, and quirks stay isolated.

- `anthropic/` — Claude API client (powers the AI layer).
- `google/` — GA4, Search Console, Google Ads, Local Services + OAuth.
- `meta/` — Meta Business (ads, pages, lead forms) + OAuth.
- `linkedin/` — LinkedIn Ads + OAuth.

Integrations only speak to the vendor and return normalized data. They pull
credentials from `app/core/config` — never hardcoded. Higher-level logic that
*uses* these clients belongs in `services/` or `ai/`.
