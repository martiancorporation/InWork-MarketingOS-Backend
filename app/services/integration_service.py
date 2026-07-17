"""Per-client integration-connection use-cases (GA4, Meta, LinkedIn, …).

Client-access scoping is enforced at the router (via ``ClientService.get_client``)
before any method here runs, so these methods take a ``client_id`` the caller is
already allowed to see and hard-filter every query by it.

**Meta is a REAL OAuth2 integration** (per-client authorization-code flow):
``oauth_start`` → the client authorizes our Meta app → ``oauth_complete`` stores
that client's own long-lived token **encrypted** on their row → ``sync`` pulls
ad insights via the Graph API into ``analytics_daily``. Tokens are only ever
persisted encrypted (``TokenCipher``) and decrypted just-in-time for a call.

The other providers still use the placeholder ``connect`` (status only, no
tokens) until their real OAuth clients are built — same shape, so the UI is
uniform. Repositories flush; this service owns the commit.
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
from app.integrations.google.oauth import GoogleOAuthClient
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
_GOOGLE_SCOPES = {
    IntegrationKey.google_ads: "https://www.googleapis.com/auth/adwords",
}
# Providers wired for REAL OAuth (others still use the placeholder `connect`).
_META_KEYS = {IntegrationKey.meta}
_REAL_KEYS = _META_KEYS | set(_GOOGLE_SCOPES)


class IntegrationService:
    def __init__(
        self,
        db: Session,
        *,
        meta_oauth: MetaOAuthClient | None = None,
        meta_client: MetaClient | None = None,
        google_oauth: GoogleOAuthClient | None = None,
        google_ads: GoogleAdsClient | None = None,
        cipher: TokenCipher | None = None,
    ) -> None:
        self.db = db
        self.integrations = IntegrationRepository(db)
        self._meta_oauth = meta_oauth
        self._meta_client = meta_client
        self._google_oauth = google_oauth
        self._google_ads = google_ads
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

    def oauth_start(
        self, client_id: uuid.UUID, key: IntegrationKey
    ) -> tuple[str, str]:
        """Begin the authorization-code flow: return (authorization_url, state).

        The operator sends the client to ``authorization_url``; the provider
        redirects back with a ``code`` the frontend hands to ``oauth_complete``."""
        self._require_real(key)
        state = self._sign_state(client_id, key)
        if key in _META_KEYS:
            url = self.meta_oauth.authorization_url(state)  # raises 503 if unconfigured
        else:
            url = self.google_oauth.authorization_url(state, _GOOGLE_SCOPES[key])
        integration = self._upsert(client_id, key)
        integration.status = IntegrationStatus.pending
        self.db.commit()
        return url, state

    async def oauth_complete(
        self, client_id: uuid.UUID, key: IntegrationKey, code: str, state: str
    ) -> Integration:
        """Finish OAuth: exchange the code, store the client's own token(s) encrypted."""
        self._require_real(key)
        if not self._verify_state(state, client_id, key):
            raise BadRequestError("Invalid or expired OAuth state.")
        integration = self._upsert(client_id, key)
        if key in _META_KEYS:
            await self._complete_meta(integration, code)
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
            if key in _META_KEYS:
                insights = await self.meta_client.fetch_insights(
                    self.cipher.decrypt(integration.access_token_encrypted),
                    integration.external_account_id or "",
                )
                platform = SocialPlatform.facebook
            else:
                access = await self._google_access_token(integration)
                insights = await self.google_ads.fetch_metrics(
                    access, integration.external_account_id or ""
                )
                platform = SocialPlatform.google
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

    # ---- per-provider OAuth completion -------------------------------- #

    async def _complete_meta(self, integration: Integration, code: str) -> None:
        oauth = self.meta_oauth
        short = await oauth.exchange_code(code)
        long_lived = await oauth.exchange_long_lived(short.get("access_token", ""))
        token = long_lived.get("access_token") or short.get("access_token")
        if not token:
            raise BadRequestError("Meta did not return an access token.")
        expires_in = long_lived.get("expires_in") or short.get("expires_in")
        accounts = await oauth.list_ad_accounts(token)
        account = accounts[0] if accounts else {}
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
        customers = await self.google_ads.list_accessible_customers(access)
        customer_id = customers[0] if customers else None
        integration.access_token_encrypted = self.cipher.encrypt(access)
        integration.refresh_token_encrypted = (
            self.cipher.encrypt(refresh) if refresh else integration.refresh_token_encrypted
        )
        integration.token_expires_at = self._expiry(tokens.get("expires_in"))
        integration.external_account_id = customer_id
        integration.account_label = customer_id
        integration.scopes = _GOOGLE_SCOPES[key]

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
    def _disconnected_view(
        client_id: uuid.UUID, key: IntegrationKey
    ) -> IntegrationRead:
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
