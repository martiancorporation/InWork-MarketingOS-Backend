"""Per-client integration-connection use-cases (GA4, Meta, LinkedIn, …).

Client-access scoping is enforced at the router (via ``ClientService.get_client``)
before any method here runs, so these methods take a ``client_id`` the caller is
already allowed to see and hard-filter every query by it.

**Meta, Google (Ads / GA4 / Search Console / LSA) and LinkedIn are REAL OAuth2
integrations** (per-client authorization-code flow): ``oauth_start`` → the client
authorizes our app → ``oauth_complete`` stores that client's own token(s)
**encrypted** on their row → ``sync`` pulls insights via the provider API into
``analytics_daily``. Tokens are only ever persisted encrypted (``TokenCipher``)
and decrypted just-in-time for a call. Providers are wired via ``_REAL_KEYS``;
when a provider's app credentials are absent the flow returns a clear 503
(never a false success). Any remaining keys use the placeholder ``connect``
(status only, no tokens) — same shape, so the UI stays uniform. Repositories
flush; this service owns the commit.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.integrations.crypto import TokenCipher
from app.integrations.google.ads import GoogleAdsClient
from app.integrations.google.ga4 import Ga4Client
from app.integrations.google.lsa import LsaClient
from app.integrations.google.oauth import GoogleOAuthClient
from app.integrations.google.search_console import SearchConsoleClient
from app.integrations.linkedin.client import LinkedInClient
from app.integrations.linkedin.oauth import LinkedInOAuthClient
from app.integrations.meta.client import MetaClient
from app.integrations.meta.oauth import MetaOAuthClient
from app.models.enums import IntegrationKey, IntegrationStatus, SocialPlatform
from app.models.integration import Integration
from app.repositories.integration_repository import IntegrationRepository
from app.schemas.analytics import AnalyticsDailyIn
from app.schemas.integration import (
    IntegrationConnectRequest,
    IntegrationListResponse,
    IntegrationRead,
)
from app.services.analytics_service import AnalyticsService

_STATE_MAX_AGE = 600  # seconds an OAuth `state` stays valid

# Google OAuth scopes per integration (one Google OAuth client, per-key scope).
# GA4 + Search Console + Ads + LSA all authorize through the same Google OAuth
# client — only the requested scope differs.
_GOOGLE_SCOPES = {
    IntegrationKey.google_ads: "https://www.googleapis.com/auth/adwords",
    IntegrationKey.google_lsa: "https://www.googleapis.com/auth/adwords",
    IntegrationKey.ga4: "https://www.googleapis.com/auth/analytics.readonly",
    IntegrationKey.search_console: "https://www.googleapis.com/auth/webmasters.readonly",
}
# Which ``analytics_daily`` platform bucket each Google integration writes into.
_GOOGLE_PLATFORM = {
    IntegrationKey.google_ads: SocialPlatform.google,
    IntegrationKey.google_lsa: SocialPlatform.google_lsa,
    IntegrationKey.ga4: SocialPlatform.ga4,
    IntegrationKey.search_console: SocialPlatform.seo,
}
# Providers wired for REAL OAuth (others still use the placeholder `connect`).
_META_KEYS = {IntegrationKey.meta}
_GOOGLE_KEYS = set(_GOOGLE_SCOPES)
_LINKEDIN_KEYS = {IntegrationKey.linkedin}
_REAL_KEYS = _META_KEYS | _GOOGLE_KEYS | _LINKEDIN_KEYS


def _select_ad_account(accounts: list[dict], requested: str | None) -> dict:
    """Pick which Meta ad account to bind after OAuth.

    - ``requested`` given → match it (``act_`` prefix optional); error if the
      authorized user can't access it (so a wrong id fails loudly).
    - none requested + exactly one account → that one.
    - none requested + several → error asking to specify (never silently guess).
    - none requested + zero → connect with no account bound.
    """
    if requested:
        want = requested.removeprefix("act_")
        for acc in accounts:
            candidates = {
                str(acc.get("account_id") or "").removeprefix("act_"),
                str(acc.get("id") or "").removeprefix("act_"),
            }
            if want in candidates:
                return acc
        raise BadRequestError(
            "The requested Meta ad account isn't accessible to the authorized user."
        )
    if not accounts:
        return {}
    if len(accounts) > 1:
        raise BadRequestError(
            "This account has multiple Meta ad accounts — pass 'ad_account_id' to pick one."
        )
    return accounts[0]


class IntegrationService:
    def __init__(
        self,
        db: Session,
        *,
        meta_oauth: MetaOAuthClient | None = None,
        meta_client: MetaClient | None = None,
        google_oauth: GoogleOAuthClient | None = None,
        google_ads: GoogleAdsClient | None = None,
        ga4_client: Ga4Client | None = None,
        search_console: SearchConsoleClient | None = None,
        lsa_client: LsaClient | None = None,
        linkedin_oauth: LinkedInOAuthClient | None = None,
        linkedin_client: LinkedInClient | None = None,
        cipher: TokenCipher | None = None,
    ) -> None:
        self.db = db
        self.integrations = IntegrationRepository(db)
        self._meta_oauth = meta_oauth
        self._meta_client = meta_client
        self._google_oauth = google_oauth
        self._google_ads = google_ads
        self._ga4_client = ga4_client
        self._search_console = search_console
        self._lsa_client = lsa_client
        self._linkedin_oauth = linkedin_oauth
        self._linkedin_client = linkedin_client
        self._cipher_override = cipher

    # ---- reads --------------------------------------------------------- #

    def list(self, client_id: uuid.UUID) -> IntegrationListResponse:
        """Return the full connector catalog.

        For every ``IntegrationKey`` return the stored row if it exists, else a
        synthesized *transient* ``disconnected`` view so the frontend can render
        all available connectors. Synthesized rows are never persisted.
        """
        stored = {i.key: i for i in self.integrations.list_for_client(client_id)}
        items = [
            IntegrationRead.model_validate(stored[key])
            if key in stored
            else self._disconnected_view(client_id, key)
            for key in IntegrationKey
        ]
        return IntegrationListResponse(items=items)

    def get(self, client_id: uuid.UUID, key: IntegrationKey) -> Integration:
        integration = self.integrations.get_for_client(client_id, key)
        if integration is None:
            raise NotFoundError("Integration not configured.")
        return integration

    # ---- writes -------------------------------------------------------- #

    def connect(
        self,
        client_id: uuid.UUID,
        key: IntegrationKey,
        data: IntegrationConnectRequest,
    ) -> Integration:
        """Simulated connect — upsert the row and flip it to ``connected``.

        No real tokens are stored (Phase-1): the ``*_encrypted`` columns stay
        NULL. Idempotent: a second connect updates the same row in place.
        """
        integration = self.integrations.get_for_client(client_id, key)
        if integration is None:
            integration = Integration(client_id=client_id, key=key)
            self.integrations.add(integration)
        integration.status = IntegrationStatus.connected
        integration.account_label = data.account_label
        integration.external_account_id = data.external_account_id
        integration.scopes = data.scopes
        integration.last_sync_at = datetime.now(UTC)
        integration.last_error = None
        self.db.commit()
        self.db.refresh(integration)
        return integration

    def disconnect(self, client_id: uuid.UUID, key: IntegrationKey) -> Integration:
        """Reset a connector to ``disconnected`` and clear any stored tokens.

        Keeps ``account_label`` so the UI can still show what it was bound to.
        """
        integration = self.get(client_id, key)
        integration.status = IntegrationStatus.disconnected
        integration.access_token_encrypted = None
        integration.refresh_token_encrypted = None
        integration.token_expires_at = None
        self.db.commit()
        self.db.refresh(integration)
        return integration

    # ---- real OAuth2 (Meta + Google Ads) ------------------------------ #

    def oauth_start(self, client_id: uuid.UUID, key: IntegrationKey) -> tuple[str, str]:
        """Begin the authorization-code flow: return (authorization_url, state).

        The operator sends the client to ``authorization_url``; the provider
        redirects back with a ``code`` the frontend hands to ``oauth_complete``."""
        self._require_real(key)
        state = self._sign_state(client_id, key)
        if key in _META_KEYS:
            url = self.meta_oauth.authorization_url(state)  # raises 503 if unconfigured
        elif key in _LINKEDIN_KEYS:
            url = self.linkedin_oauth.authorization_url(state)  # raises 503 if unconfigured
        else:
            url = self.google_oauth.authorization_url(state, _GOOGLE_SCOPES[key])
        integration = self._upsert(client_id, key)
        integration.status = IntegrationStatus.pending
        self.db.commit()
        return url, state

    async def oauth_complete(
        self,
        client_id: uuid.UUID,
        key: IntegrationKey,
        code: str,
        state: str,
        *,
        ad_account_id: str | None = None,
    ) -> Integration:
        """Finish OAuth: exchange the code, store the client's own token(s) encrypted."""
        self._require_real(key)
        if not self._verify_state(state, client_id, key):
            raise BadRequestError("Invalid or expired OAuth state.")
        integration = self._upsert(client_id, key)
        if key in _META_KEYS:
            await self._complete_meta(integration, code, ad_account_id=ad_account_id)
        elif key in _LINKEDIN_KEYS:
            await self._complete_linkedin(integration, code)
        else:
            await self._complete_google(integration, key, code)
        integration.status = IntegrationStatus.connected
        integration.last_error = None
        self.db.commit()
        self.db.refresh(integration)
        return integration

    async def sync(self, client_id: uuid.UUID, key: IntegrationKey) -> Integration:
        """Pull live insights from the provider into ``analytics_daily``."""
        self._require_real(key)
        integration = self.get(client_id, key)  # 404 if never configured
        if (
            integration.status != IntegrationStatus.connected
            or not integration.access_token_encrypted
        ):
            raise BadRequestError("Integration is not connected — run OAuth first.")
        try:
            insights, platform = await self._fetch_insights(integration, key)
        except Exception as exc:
            integration.status = IntegrationStatus.error
            integration.last_error = str(exc)[:1000]
            self.db.commit()
            raise
        # Upsert today's facts for the provider's platform (own transaction).
        AnalyticsService(self.db).ingest(
            client_id, [AnalyticsDailyIn(date=date.today(), platform=platform, **insights)]
        )
        integration.status = IntegrationStatus.connected
        integration.last_sync_at = datetime.now(UTC)
        integration.last_error = None
        self.db.commit()
        self.db.refresh(integration)
        return integration

    # ---- per-provider sync dispatch ----------------------------------- #

    async def _fetch_insights(
        self, integration: Integration, key: IntegrationKey
    ) -> tuple[dict, SocialPlatform]:
        """Pull normalized insights for a connected integration + its platform."""
        account = integration.external_account_id or ""
        if key in _META_KEYS:
            token = self.cipher.decrypt(integration.access_token_encrypted)
            return await self.meta_client.fetch_insights(token, account), SocialPlatform.facebook
        if key in _LINKEDIN_KEYS:
            token = await self._linkedin_access_token(integration)
            return await self.linkedin_client.fetch_metrics(token, account), SocialPlatform.linkedin
        # Google family (Ads / LSA / GA4 / Search Console) — shared OAuth token.
        access = await self._google_access_token(integration)
        if key == IntegrationKey.google_ads:
            insights = await self.google_ads.fetch_metrics(access, account)
        elif key == IntegrationKey.google_lsa:
            insights = await self.lsa_client.fetch_metrics(access, account)
        elif key == IntegrationKey.ga4:
            insights = await self.ga4_client.fetch_metrics(access, account)
        elif key == IntegrationKey.search_console:
            insights = await self.search_console.fetch_metrics(access, account)
        else:  # pragma: no cover - guarded by _require_real
            raise BadRequestError(f"Sync is not implemented for '{key.value}'.")
        return insights, _GOOGLE_PLATFORM[key]

    # ---- per-provider OAuth completion -------------------------------- #

    async def _complete_meta(
        self, integration: Integration, code: str, *, ad_account_id: str | None = None
    ) -> None:
        oauth = self.meta_oauth
        short = await oauth.exchange_code(code)
        long_lived = await oauth.exchange_long_lived(short.get("access_token", ""))
        token = long_lived.get("access_token") or short.get("access_token")
        if not token:
            raise BadRequestError("Meta did not return an access token.")
        expires_in = long_lived.get("expires_in") or short.get("expires_in")
        accounts = await oauth.list_ad_accounts(token)
        account = _select_ad_account(accounts, ad_account_id)
        integration.access_token_encrypted = self.cipher.encrypt(token)
        integration.refresh_token_encrypted = None  # Meta long-lived tokens self-renew
        integration.token_expires_at = self._expiry(expires_in)
        integration.external_account_id = account.get("account_id") or account.get("id")
        integration.account_label = account.get("name")
        integration.scopes = get_settings().integrations.meta_scopes

    async def _complete_google(
        self, integration: Integration, key: IntegrationKey, code: str
    ) -> None:
        tokens = await self.google_oauth.exchange_code(code)
        access = tokens.get("access_token")
        if not access:
            raise BadRequestError("Google did not return an access token.")
        refresh = tokens.get("refresh_token")
        account_id = await self._discover_google_account(key, access)
        integration.access_token_encrypted = self.cipher.encrypt(access)
        integration.refresh_token_encrypted = (
            self.cipher.encrypt(refresh) if refresh else integration.refresh_token_encrypted
        )
        integration.token_expires_at = self._expiry(tokens.get("expires_in"))
        integration.external_account_id = account_id
        integration.account_label = account_id
        integration.scopes = _GOOGLE_SCOPES[key]

    async def _discover_google_account(self, key: IntegrationKey, access: str) -> str | None:
        """Bind the first account/property/site the token can read for ``key``."""
        if key == IntegrationKey.google_ads:
            found = await self.google_ads.list_accessible_customers(access)
        elif key == IntegrationKey.google_lsa:
            found = await self.lsa_client.list_accessible_customers(access)
        elif key == IntegrationKey.ga4:
            found = await self.ga4_client.list_properties(access)
        elif key == IntegrationKey.search_console:
            found = await self.search_console.list_sites(access)
        else:  # pragma: no cover - guarded by _require_real
            found = []
        return found[0] if found else None

    async def _complete_linkedin(self, integration: Integration, code: str) -> None:
        tokens = await self.linkedin_oauth.exchange_code(code)
        access = tokens.get("access_token")
        if not access:
            raise BadRequestError("LinkedIn did not return an access token.")
        refresh = tokens.get("refresh_token")
        accounts = await self.linkedin_client.list_ad_accounts(access)
        account = accounts[0] if accounts else {}
        integration.access_token_encrypted = self.cipher.encrypt(access)
        integration.refresh_token_encrypted = self.cipher.encrypt(refresh) if refresh else None
        integration.token_expires_at = self._expiry(tokens.get("expires_in"))
        integration.external_account_id = account.get("id")
        integration.account_label = account.get("name") or account.get("id")
        integration.scopes = get_settings().integrations.linkedin_scopes

    async def _linkedin_access_token(self, integration: Integration) -> str:
        """Return a valid LinkedIn access token, refreshing if near expiry."""
        now = datetime.now(UTC)
        expires = integration.token_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        fresh = expires is None or expires > now + timedelta(seconds=60)
        if fresh or not integration.refresh_token_encrypted:
            return self.cipher.decrypt(integration.access_token_encrypted)
        tokens = await self.linkedin_oauth.refresh_access_token(
            self.cipher.decrypt(integration.refresh_token_encrypted)
        )
        access = tokens.get("access_token")
        if not access:  # refresh failed — fall back to the stored token
            return self.cipher.decrypt(integration.access_token_encrypted)
        integration.access_token_encrypted = self.cipher.encrypt(access)
        integration.token_expires_at = self._expiry(tokens.get("expires_in"))
        self.db.commit()
        return access

    async def _google_access_token(self, integration: Integration) -> str:
        """Return a valid Google access token, refreshing it if it's near expiry."""
        now = datetime.now(UTC)
        expires = integration.token_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        fresh = expires is None or expires > now + timedelta(seconds=60)
        if fresh or not integration.refresh_token_encrypted:
            return self.cipher.decrypt(integration.access_token_encrypted)
        tokens = await self.google_oauth.refresh_access_token(
            self.cipher.decrypt(integration.refresh_token_encrypted)
        )
        access = tokens.get("access_token")
        if not access:  # refresh failed — fall back to the stored token
            return self.cipher.decrypt(integration.access_token_encrypted)
        integration.access_token_encrypted = self.cipher.encrypt(access)
        integration.token_expires_at = self._expiry(tokens.get("expires_in"))
        self.db.commit()
        return access

    # ---- helpers ------------------------------------------------------- #

    @property
    def meta_oauth(self) -> MetaOAuthClient:
        if self._meta_oauth is None:
            self._meta_oauth = MetaOAuthClient()
        return self._meta_oauth

    @property
    def meta_client(self) -> MetaClient:
        if self._meta_client is None:
            self._meta_client = MetaClient()
        return self._meta_client

    @property
    def google_oauth(self) -> GoogleOAuthClient:
        if self._google_oauth is None:
            self._google_oauth = GoogleOAuthClient()
        return self._google_oauth

    @property
    def google_ads(self) -> GoogleAdsClient:
        if self._google_ads is None:
            self._google_ads = GoogleAdsClient()
        return self._google_ads

    @property
    def ga4_client(self) -> Ga4Client:
        if self._ga4_client is None:
            self._ga4_client = Ga4Client()
        return self._ga4_client

    @property
    def search_console(self) -> SearchConsoleClient:
        if self._search_console is None:
            self._search_console = SearchConsoleClient()
        return self._search_console

    @property
    def lsa_client(self) -> LsaClient:
        if self._lsa_client is None:
            self._lsa_client = LsaClient()
        return self._lsa_client

    @property
    def linkedin_oauth(self) -> LinkedInOAuthClient:
        if self._linkedin_oauth is None:
            self._linkedin_oauth = LinkedInOAuthClient()
        return self._linkedin_oauth

    @property
    def linkedin_client(self) -> LinkedInClient:
        if self._linkedin_client is None:
            self._linkedin_client = LinkedInClient()
        return self._linkedin_client

    @property
    def cipher(self) -> TokenCipher:
        if self._cipher_override is None:
            self._cipher_override = TokenCipher()
        return self._cipher_override

    @staticmethod
    def _expiry(expires_in) -> datetime | None:
        return datetime.now(UTC) + timedelta(seconds=int(expires_in)) if expires_in else None

    @staticmethod
    def _require_real(key: IntegrationKey) -> None:
        if key not in _REAL_KEYS:
            raise BadRequestError(
                f"Real OAuth is not yet available for '{key.value}'. "
                f"Use the connect endpoint for that provider until its client is built."
            )

    def _upsert(self, client_id: uuid.UUID, key: IntegrationKey) -> Integration:
        integration = self.integrations.get_for_client(client_id, key)
        if integration is None:
            integration = Integration(client_id=client_id, key=key)
            self.integrations.add(integration)
            self.db.flush()
        return integration

    @staticmethod
    def _sign_state(client_id: uuid.UUID, key: IntegrationKey) -> str:
        raw = f"{client_id}:{key.value}:{int(time.time())}"
        sig = _hmac(raw)
        return base64.urlsafe_b64encode(f"{raw}:{sig}".encode()).decode()

    @staticmethod
    def _verify_state(state: str, client_id: uuid.UUID, key: IntegrationKey) -> bool:
        try:
            decoded = base64.urlsafe_b64decode(state.encode()).decode()
            cid, k, ts, sig = decoded.rsplit(":", 3)
        except Exception:
            return False
        if not hmac.compare_digest(sig, _hmac(f"{cid}:{k}:{ts}")):
            return False
        if cid != str(client_id) or k != key.value:
            return False
        try:
            return (int(time.time()) - int(ts)) <= _STATE_MAX_AGE
        except ValueError:
            return False

    @staticmethod
    def _disconnected_view(client_id: uuid.UUID, key: IntegrationKey) -> IntegrationRead:
        """A transient ``disconnected`` catalog entry for a never-configured key."""
        now = datetime.now(UTC)
        return IntegrationRead(
            id=uuid.uuid4(),
            client_id=client_id,
            key=key,
            status=IntegrationStatus.disconnected,
            account_label=None,
            external_account_id=None,
            scopes=None,
            last_sync_at=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )


def _hmac(raw: str) -> str:
    """Truncated HMAC-SHA256 of ``raw`` keyed by SECRET_KEY (OAuth state signing)."""
    secret = get_settings().security.secret_key.encode()
    return hmac.new(secret, raw.encode(), hashlib.sha256).hexdigest()[:32]
