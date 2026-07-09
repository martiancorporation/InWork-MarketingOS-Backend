# app/integrations/google/

Clients for Google's marketing/analytics APIs plus the shared OAuth flow.

- `oauth.py` — Google OAuth handshake (read-only scopes).
- `ga4.py` — Google Analytics 4.
- `search_console.py` — Search Console.
- `ads.py` — Google Ads.
- `lsa.py` — Google Local Services Ads.

All connections are read-only. Credentials come from config, never hardcoded.
