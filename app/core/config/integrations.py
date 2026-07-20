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
    # Google Ads-specific: developer token (Google-approved) + manager (MCC) id.
    google_developer_token: str | None = None
    google_login_customer_id: str | None = None  # MCC customer id, digits only
    google_ads_api_version: str = "v18"

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
    linkedin_api_version: str = "202401"
    linkedin_scopes: str = "r_ads,r_ads_reporting"

    @property
    def linkedin_configured(self) -> bool:
        return bool(
            self.linkedin_client_id
            and self.linkedin_client_secret
            and self.linkedin_redirect_uri
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
