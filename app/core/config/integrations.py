"""Third-party OAuth credentials (Google, Meta, LinkedIn).

All optional/None in local dev — populated per environment. Consumed by the
clients in ``app/integrations/`` when those connections are implemented.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class IntegrationsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES, env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None
    # Google Ads-specific: developer token (Google-approved). The manager
    # (MCC) id to send as login-customer-id is per-account, not global — see
    # app/integrations/google/account_structure.py.
    google_developer_token: str | None = None
    google_ads_api_version: str = "v22"

    @property
    def google_configured(self) -> bool:
        return bool(
            self.google_client_id and self.google_client_secret and self.google_redirect_uri
        )

    @property
    def google_ads_configured(self) -> bool:
        return self.google_configured and bool(self.google_developer_token)

    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_redirect_uri: str | None = None
    meta_api_version: str = "v21.0"
    meta_scopes: str = "ads_read,business_management"

    linkedin_client_id: str | None = None
    linkedin_client_secret: str | None = None
    linkedin_redirect_uri: str | None = None
    # LinkedIn Marketing API version (monthly, YYYYMM) + ads OAuth scopes.
    # LinkedIn only supports each monthly version for ~1 year — this default
    # rots; verify it against LinkedIn's currently-supported versions
    # (https://learn.microsoft.com/linkedin/marketing/versioning) rather than
    # trusting it blindly, and bump it periodically either way.
    linkedin_api_version: str = "202506"
    linkedin_scopes: str = "r_ads,r_ads_reporting"

    @property
    def linkedin_configured(self) -> bool:
        return bool(
            self.linkedin_client_id and self.linkedin_client_secret and self.linkedin_redirect_uri
        )

    # Scraping / research providers (used by brand extraction). Optional — the
    # extractor falls back to a headless render / httpx scrape when unset.
    scrapingbee_api_key: str | None = None  # proxied, JS-rendering fetch (beats IP/anti-bot blocks)
    brave_api_key: str | None = None  # Brave Search API for brand research

    @property
    def scrapingbee_configured(self) -> bool:
        return bool(self.scrapingbee_api_key)

    @property
    def brave_configured(self) -> bool:
        return bool(self.brave_api_key)

    @property
    def meta_configured(self) -> bool:
        return bool(self.meta_app_id and self.meta_app_secret and self.meta_redirect_uri)

    # GoHighLevel / LeadConnector — a real Marketplace App, authorization-code
    # OAuth2 flow (https://marketplace.gohighlevel.com/docs/Authorization/OAuth2.0/),
    # same shape as Meta/Google below. One agency-wide connection (this
    # engagement's GHL setup is one shared location for every client — see
    # app/models/ghl_agency_connection.py), not one per client.
    ghl_client_id: str | None = None
    ghl_client_secret: str | None = None
    ghl_redirect_uri: str | None = None
    ghl_base_url: str = "https://services.leadconnectorhq.com"
    ghl_authorize_url: str = "https://marketplace.gohighlevel.com/oauth/chooselocation"
    ghl_api_version: str = "2021-07-28"
    # Space-separated, per GHL's documented format. contacts.readonly is all
    # the current GhlClient needs (contact search); extend here (never
    # hardcode a second copy elsewhere) if a future feature needs more.
    ghl_scopes: str = "contacts.readonly locations.readonly"

    @property
    def ghl_configured(self) -> bool:
        return bool(self.ghl_client_id and self.ghl_client_secret and self.ghl_redirect_uri)
