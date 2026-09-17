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
import logging
import time
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError, BadRequestError, NotFoundError, ProviderAuthError
from app.integrations.crypto import TokenCipher
from app.integrations.ghl.client import GhlClient, GhlContactsPage
from app.integrations.ghl.oauth import GhlOAuthClient
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
from app.services.analytics_breakdown_service import AnalyticsBreakdownService
from app.services.analytics_service import AnalyticsService
from app.services.platform_insight_service import PlatformInsightService

_STATE_MAX_AGE = 600  # seconds an OAuth `state` stays valid
logger = logging.getLogger("app.services.integration_service")

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
# Providers wired for REAL OAuth (an authorization-code redirect through our
# own app — oauth_start / oauth_complete). GHL is deliberately NOT one of
# these: its token is handed to us directly, out-of-band, by the client's
# team (see `connect_ghl`), so it never runs the redirect flow and must not
# be picked up by oauth_start/oauth_complete's per-provider branching, nor by
# the scheduler sweep's generic `sync()` call (which only knows ad-metrics
# providers) — a separate, explicit set keeps it out of both.
_META_KEYS = {IntegrationKey.meta}
_GOOGLE_KEYS = set(_GOOGLE_SCOPES)
_LINKEDIN_KEYS = {IntegrationKey.linkedin}
_REAL_KEYS = _META_KEYS | _GOOGLE_KEYS | _LINKEDIN_KEYS
_GHL_KEYS = {IntegrationKey.ghl}


def _select_ad_account(accounts: list[dict], requested: str | None) -> dict | None:
    """Pick which Meta ad account to bind after OAuth.

    - ``requested`` given → match it (``act_`` prefix optional); error if the
      authorized user can't access it (so a wrong id fails loudly).
    - none requested + zero accounts → connect with no account bound (``{}``).
    - none requested + one or more accounts → ``None`` (always ask — never
      auto-bind, even when there's only one, since the authorized user may
      have access to more accounts than a single OAuth call surfaces; the
      caller surfaces the full list instead of silently guessing — see
      ``select_account``).
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
    return None


def _select_google_account(accounts: list[str], requested: str | None) -> str | None:
    """Pick which Google account (Ads customer id / GA4 property / Search
    Console site) to bind — same "never auto-bind" contract as
    ``_select_ad_account``, just over a plain id list instead of dicts (Google's
    discovery calls don't return display names).
    """
    if requested:
        want = requested.replace("-", "")
        for acc in accounts:
            if acc.replace("-", "") == want:
                return acc
        raise BadRequestError(
            "The requested Google account isn't accessible to the authorized user."
        )
    return None  # zero accounts, or one-or-more unpicked — always ask


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
        ghl_oauth: GhlOAuthClient | None = None,
        ghl_client: GhlClient | None = None,
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
        self._ghl_oauth = ghl_oauth
        self._ghl_client = ghl_client
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

    # ---- GHL (token handed to us directly — no redirect flow) --------- #

    def connect_ghl(
        self,
        client_id: uuid.UUID,
        *,
        access_token: str,
        refresh_token: str | None,
        location_id: str,
        tags: list[str],
        expires_in: int | None = None,
    ) -> Integration:
        """Store a GHL Private App token handed to us out-of-band.

        Unlike Meta/Google/LinkedIn, there is no authorization-code redirect
        through our app for GHL — the client's team issues the access/refresh
        token pair directly (see the account-access email thread), scoped to
        their one shared location. ``tags`` is the set of contact/opportunity
        tags that identify this client's records within that shared location.
        """
        if not tags:
            raise BadRequestError("At least one GHL tag is required to connect this client.")
        integration = self._upsert(client_id, IntegrationKey.ghl)
        integration.status = IntegrationStatus.connected
        integration.external_account_id = location_id
        integration.ghl_tags = tags
        integration.access_token_encrypted = self.cipher.encrypt(access_token)
        integration.refresh_token_encrypted = (
            self.cipher.encrypt(refresh_token) if refresh_token else None
        )
        integration.token_expires_at = self._expiry(expires_in)
        integration.last_error = None
        self.db.commit()
        self.db.refresh(integration)
        return integration

    async def fetch_ghl_contacts(
        self,
        client_id: uuid.UUID,
        *,
        page_limit: int = 100,
        search_after: list | None = None,
    ) -> GhlContactsPage:
        """One page of contacts tagged for this client, from InWork's one
        shared GHL location. Bounded like every other list endpoint — callers
        that want everything page through via ``next_search_after`` rather
        than this pulling an unbounded set into memory in one call."""
        integration = self.get(client_id, IntegrationKey.ghl)  # 404 if never connected
        if not integration.access_token_encrypted or not integration.external_account_id:
            raise BadRequestError("GHL is not connected for this client.")
        if not integration.ghl_tags:
            raise BadRequestError("No GHL tags are configured for this client.")
        try:
            token = await self._ghl_access_token(integration)
            page = await self.ghl_client.search_contacts(
                token,
                integration.external_account_id,
                integration.ghl_tags,
                page_limit=page_limit,
                search_after=search_after,
            )
        except Exception as exc:
            # Same split as `sync()`: a dead grant (refresh token itself
            # expired/revoked — GHL's refresh tokens are valid up to a year if
            # unused, per the account-access email thread) needs a reconnect;
            # anything else is worth retrying, so it stays `error`.
            integration.status = (
                IntegrationStatus.needs_reauth
                if isinstance(exc, ProviderAuthError)
                else IntegrationStatus.error
            )
            integration.last_error = str(exc)[:1000]
            self.db.commit()
            raise
        integration.status = IntegrationStatus.connected
        integration.last_sync_at = datetime.now(UTC)
        integration.last_error = None
        self.db.commit()
        return page

    async def _ghl_access_token(self, integration: Integration) -> str:
        """Return a valid GHL access token, refreshing it if it's near expiry.

        GHL rotates the refresh token on every use — the new one returned by
        the refresh call is what must be persisted, not the one just spent.
        """
        now = datetime.now(UTC)
        expires = integration.token_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        fresh = expires is None or expires > now + timedelta(seconds=60)
        if fresh or not integration.refresh_token_encrypted:
            return self.cipher.decrypt(integration.access_token_encrypted)
        tokens = await self.ghl_oauth.refresh_access_token(
            self.cipher.decrypt(integration.refresh_token_encrypted)
        )
        access = tokens.get("access_token")
        if not access:  # refresh failed — fall back to the stored token
            return self.cipher.decrypt(integration.access_token_encrypted)
        integration.access_token_encrypted = self.cipher.encrypt(access)
        new_refresh = tokens.get("refresh_token")
        if new_refresh:
            integration.refresh_token_encrypted = self.cipher.encrypt(new_refresh)
        integration.token_expires_at = self._expiry(tokens.get("expires_in"))
        self.db.commit()
        return access

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
        login_customer_id: str | None = None,
    ) -> tuple[Integration, list[dict]]:
        """Finish OAuth: exchange the code, store the client's own token(s) encrypted.

        Returns ``(integration, pending_accounts)`` — ``pending_accounts`` is
        non-empty when Meta or a Google-family provider found one or more
        accounts and none was picked yet (never auto-bound, even when there's
        only one — see ``_select_ad_account``/``_select_google_account``); the
        connection still succeeds (no account bound), and the caller should
        finish with ``select_account``.
        """
        self._require_real(key)
        if not self._verify_state(state, client_id, key):
            raise BadRequestError("Invalid or expired OAuth state.")
        integration = self._upsert(client_id, key)
        pending_accounts: list[dict] = []
        if key in _META_KEYS:
            pending_accounts = await self._complete_meta(
                integration, code, ad_account_id=ad_account_id
            )
        elif key in _LINKEDIN_KEYS:
            await self._complete_linkedin(integration, code)
        else:
            pending_accounts = await self._complete_google(
                integration,
                key,
                code,
                ad_account_id=ad_account_id,
                login_customer_id=login_customer_id,
            )
        integration.status = IntegrationStatus.connected
        integration.last_error = None
        self.db.commit()
        self.db.refresh(integration)
        if not pending_accounts:
            # An account is already bound (single-account Meta, or any other
            # provider) — pull data right away instead of leaving the operator
            # to find and click "Sync" separately.
            integration = await self._try_sync_after_connect(client_id, key, integration)
        return integration, pending_accounts

    async def select_account(
        self,
        client_id: uuid.UUID,
        key: IntegrationKey,
        ad_account_id: str,
        *,
        login_customer_id: str | None = None,
    ) -> Integration:
        """Finish binding an ad account/property/site after ``oauth_complete``
        came back ambiguous. Uses the already-stored token — no new OAuth
        round-trip (the provider's ``code`` is single-use)."""
        if key not in _META_KEYS and key not in _GOOGLE_KEYS:
            raise BadRequestError("Ad account selection is not available for this provider.")
        integration = self.get(client_id, key)  # 404 if never configured
        if (
            integration.status != IntegrationStatus.connected
            or not integration.access_token_encrypted
        ):
            raise BadRequestError("Integration is not connected — run OAuth first.")
        if key in _META_KEYS:
            token = self.cipher.decrypt(integration.access_token_encrypted)
            accounts = await self.meta_oauth.list_ad_accounts(token)
            account = _select_ad_account(accounts, ad_account_id)
            if not account:
                raise BadRequestError(
                    "The requested Meta ad account isn't accessible to the authorized user."
                )
            integration.external_account_id = account.get("account_id") or account.get("id")
            integration.account_label = account.get("name")
        else:
            access = await self._google_access_token(integration)
            accounts = await self._list_google_accounts(key, access)
            account_id = _select_google_account(accounts, ad_account_id)
            if account_id is None:
                raise BadRequestError(
                    "The requested Google account isn't accessible to the authorized user."
                )
            integration.external_account_id = account_id
            integration.account_label = account_id
            if key == IntegrationKey.google_ads:
                integration.login_customer_id = login_customer_id
        self.db.commit()
        self.db.refresh(integration)
        # The account is only known now — this is the first point a sync can
        # actually pull anything, so do it immediately rather than waiting for
        # the operator to notice and click "Sync".
        return await self._try_sync_after_connect(client_id, key, integration)

    async def _try_sync_after_connect(
        self, client_id: uuid.UUID, key: IntegrationKey, integration: Integration
    ) -> Integration:
        """Best-effort immediate sync right after connecting. The connect
        itself already succeeded (a valid token is stored) — a failed
        first-sync attempt (e.g. a still-missing developer token) must not
        look like the *connection* failed, so this deliberately overrides
        ``sync``'s own error-status side effect back to ``connected``,
        keeping ``last_error`` as a hint that a manual "Sync" retry is
        needed."""
        try:
            return await self.sync(client_id, key)
        except Exception as exc:
            logger.warning(
                "Auto-sync after connect failed for client %s key %s",
                client_id,
                key.value,
                exc_info=True,
            )
            integration.status = IntegrationStatus.connected
            integration.last_error = f"Connected, but the first automatic sync failed: {exc}"[:1000]
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
        if (key in _META_KEYS or key in _GOOGLE_KEYS) and not integration.external_account_id:
            # Connected (token stored), but the ambiguous-accounts case was
            # never finished with select_account — nothing bound to query yet.
            raise BadRequestError(
                "No account is bound yet — finish account selection before syncing."
            )
        try:
            rows, platform = await self._fetch_insights(integration, key)
        except Exception as exc:
            # A dead OAuth grant (ProviderAuthError, raised by the provider
            # clients) needs a reconnect; anything else — a network blip, a
            # rate limit, a bad request — is worth retrying, so it stays
            # `error`. Without that split an operator can't tell the two apart
            # without reading `last_error` prose.
            integration.status = (
                IntegrationStatus.needs_reauth
                if isinstance(exc, ProviderAuthError)
                else IntegrationStatus.error
            )
            integration.last_error = str(exc)[:1000]
            self.db.commit()
            raise
        # Upsert every day's facts for the provider's platform. ``commit=False``
        # so these rows are no longer durably persisted *on their own* ahead of
        # everything else: a crash before the terminal commit below now leaves
        # a coherent "this sync didn't happen" state (the next sync re-pulls
        # the same provider data) instead of orphaned analytics rows next to an
        # integration that still looks unsynced.
        #
        # Caveat, deliberate: for the keys that follow up with an additive sync
        # (`_sync_platform_insights` / `_sync_analytics_breakdowns`), those
        # services own their own commit, which lands these rows together with
        # the insight rows — before the status update below. Only LinkedIn
        # (neither branch) is fully atomic end-to-end. Closing that last gap
        # means giving the additive syncs a SAVEPOINT so their best-effort
        # failure can roll back without discarding these rows; not worth the
        # redesign for a window whose worst case is a stale `last_sync_at`.
        #
        # Meta returns one row per day (last_90d); every other provider still
        # returns a single row dated today until they grow day-level pulls too.
        AnalyticsService(self.db).ingest(
            client_id,
            [AnalyticsDailyIn(platform=platform, **row) for row in rows],
            commit=False,
        )
        if key in _META_KEYS or key in (IntegrationKey.google_ads, IntegrationKey.google_lsa):
            await self._sync_platform_insights(client_id, key, integration)
        elif key in (IntegrationKey.ga4, IntegrationKey.search_console):
            await self._sync_analytics_breakdowns(client_id, key, integration)
        integration.status = IntegrationStatus.connected
        integration.last_sync_at = datetime.now(UTC)
        integration.last_error = None
        self.db.commit()
        self.db.refresh(integration)
        return integration

    async def _sync_platform_insights(
        self, client_id: uuid.UUID, key: IntegrationKey, integration: Integration
    ) -> None:
        """Best-effort: pull the richer campaign/ad-set/ad/recommendation data
        into the Platform Insights tables. Additive to the core
        ``analytics_daily`` sync above, which has already succeeded by the
        time this runs — a failure here must not flip the whole sync to
        ``error`` (same reasoning as ``_try_sync_after_connect``)."""
        try:
            token = self.cipher.decrypt(integration.access_token_encrypted)
            service = PlatformInsightService(self.db)
            if key == IntegrationKey.google_ads:
                await service.sync_google_ads(
                    client_id,
                    self.google_ads,
                    token,
                    integration.external_account_id,
                    login_customer_id=integration.login_customer_id,
                )
            elif key == IntegrationKey.google_lsa:
                await service.sync_google_lsa(
                    client_id, self.lsa_client, token, integration.external_account_id
                )
            else:
                await service.sync_meta(
                    client_id, self.meta_client, token, integration.external_account_id
                )
        except Exception:
            logger.warning(
                "Platform insights sync failed for client %s key %s",
                client_id,
                integration.key.value,
                exc_info=True,
            )

    async def _sync_analytics_breakdowns(
        self, client_id: uuid.UUID, key: IntegrationKey, integration: Integration
    ) -> None:
        """Best-effort: pull GA4/Search Console's own dimensional breakdowns
        (top pages, channels, devices, queries) — additive to the core
        ``analytics_daily`` sync above, same non-fatal-failure reasoning as
        ``_sync_platform_insights``."""
        try:
            token = self.cipher.decrypt(integration.access_token_encrypted)
            service = AnalyticsBreakdownService(self.db)
            if key == IntegrationKey.ga4:
                await service.sync_ga4(
                    client_id, self.ga4_client, token, integration.external_account_id
                )
            else:
                await service.sync_search_console(
                    client_id, self.search_console, token, integration.external_account_id
                )
        except Exception:
            logger.warning(
                "Analytics breakdown sync failed for client %s key %s",
                client_id,
                integration.key.value,
                exc_info=True,
            )

    # ---- per-provider sync dispatch ----------------------------------- #

    async def _fetch_insights(
        self, integration: Integration, key: IntegrationKey
    ) -> tuple[list[dict], SocialPlatform]:
        """Pull normalized insight rows for a connected integration + its platform.

        Each row already carries its own ``date`` key (see ``AnalyticsDailyIn``).
        """
        account = integration.external_account_id or ""
        if key in _META_KEYS:
            token = await self._meta_access_token(integration)
            rows = await self.meta_client.fetch_daily_insights(token, account)
            return rows, SocialPlatform.facebook
        if key in _LINKEDIN_KEYS:
            token = await self._linkedin_access_token(integration)
            insights = await self.linkedin_client.fetch_metrics(token, account)
            return [{"date": date.today(), **insights}], SocialPlatform.linkedin
        # Google family (Ads / LSA / GA4 / Search Console) — shared OAuth token,
        # each pulling its own day-by-day series.
        access = await self._google_access_token(integration)
        if key == IntegrationKey.google_ads:
            rows = await self.google_ads.fetch_daily_insights(
                access, account, login_customer_id=integration.login_customer_id
            )
        elif key == IntegrationKey.google_lsa:
            rows = await self.lsa_client.fetch_daily_insights(access, account)
        elif key == IntegrationKey.ga4:
            rows = await self.ga4_client.fetch_daily_insights(access, account)
        elif key == IntegrationKey.search_console:
            rows = await self.search_console.fetch_daily_insights(access, account)
        else:  # pragma: no cover - guarded by _require_real
            raise BadRequestError(f"Sync is not implemented for '{key.value}'.")
        return rows, _GOOGLE_PLATFORM[key]

    # ---- per-provider OAuth completion -------------------------------- #

    async def _complete_meta(
        self, integration: Integration, code: str, *, ad_account_id: str | None = None
    ) -> list[dict]:
        """Returns the accounts list when ambiguous (see ``_select_ad_account``),
        else ``[]`` — the account (if any) is already bound on ``integration``."""
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
        # Meta has no separate refresh token — a long-lived access token
        # (~60 days) is renewed by re-exchanging itself via the same
        # fb_exchange_token endpoint (see _meta_access_token), not via a
        # refresh grant. It does NOT renew on its own; sync() proactively
        # re-extends it as it approaches expiry.
        integration.refresh_token_encrypted = None
        integration.token_expires_at = self._expiry(expires_in)
        integration.scopes = get_settings().integrations.meta_scopes
        if account is None:
            integration.external_account_id = None
            integration.account_label = None
            return accounts
        integration.external_account_id = account.get("account_id") or account.get("id")
        integration.account_label = account.get("name")
        return []

    async def _complete_google(
        self,
        integration: Integration,
        key: IntegrationKey,
        code: str,
        *,
        ad_account_id: str | None = None,
        login_customer_id: str | None = None,
    ) -> list[dict]:
        """Returns the accounts list when ambiguous (see
        ``_select_google_account``), else ``[]`` — the account (if any) is
        already bound on ``integration``. Same "never auto-bind" contract as
        ``_complete_meta``."""
        tokens = await self.google_oauth.exchange_code(code)
        access = tokens.get("access_token")
        if not access:
            raise BadRequestError("Google did not return an access token.")
        refresh = tokens.get("refresh_token")
        accounts = await self._list_google_accounts(key, access)
        account_id = _select_google_account(accounts, ad_account_id)
        integration.access_token_encrypted = self.cipher.encrypt(access)
        integration.refresh_token_encrypted = (
            self.cipher.encrypt(refresh) if refresh else integration.refresh_token_encrypted
        )
        integration.token_expires_at = self._expiry(tokens.get("expires_in"))
        integration.scopes = _GOOGLE_SCOPES[key]
        if account_id is None:
            integration.external_account_id = None
            integration.account_label = None
            return [{"id": a, "name": a} for a in accounts]
        integration.external_account_id = account_id
        integration.account_label = account_id
        # Google Ads only: the operator supplies this per real account (which
        # accounts sit under an MCC is client-specific, not derivable via the
        # API) — see app/models/integration.py.
        if key == IntegrationKey.google_ads:
            integration.login_customer_id = login_customer_id
        return []

    async def _list_google_accounts(self, key: IntegrationKey, access: str) -> list[str]:
        """Every account/property/site the token can read for ``key`` — no
        picking here, see ``_select_google_account``."""
        if key == IntegrationKey.google_ads:
            return await self._list_ads_family_customers(self.google_ads, access)
        if key == IntegrationKey.google_lsa:
            return await self._list_ads_family_customers(self.lsa_client, access)
        if key == IntegrationKey.ga4:
            return await self.ga4_client.list_properties(access)
        if key == IntegrationKey.search_console:
            return await self.search_console.list_sites(access)
        return []  # pragma: no cover - guarded by _require_real

    async def _list_ads_family_customers(
        self, ads_api_client: GoogleAdsClient | LsaClient, access: str
    ) -> list[str]:
        """Shared by Google Ads and LSA — both ride the same underlying Ads
        API account model. ``listAccessibleCustomers`` only returns accounts
        the OAuth user has *direct* access to — a manager account's linked
        clients (e.g. a client account we were invited into via the Google
        Ads UI, not via user-level access) don't show up there at all. Expand
        every directly-accessible account through ``customer_client`` so a
        manager-linked client account is selectable too, not just the manager
        account itself."""
        direct = await ads_api_client.list_accessible_customers(access)
        ids = list(direct)
        seen = set(direct)
        for manager_id in direct:
            try:
                children = await ads_api_client.list_customer_clients(access, manager_id)
            except AppError:
                # Not every accessible account is a manager — a permission or
                # query error here just means this one has no linked clients.
                logger.info("No linked client accounts under %s (or not a manager).", manager_id)
                continue
            for child in children:
                cid = child.get("id")
                if cid and cid not in seen:
                    seen.add(cid)
                    ids.append(cid)
        return ids

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

    async def _meta_access_token(self, integration: Integration) -> str:
        """Return a valid Meta access token, proactively re-extending it if
        it's approaching its ~60-day expiry.

        Meta has no refresh-token grant — the same long-lived token is
        re-exchanged for a fresh one via ``fb_exchange_token`` (the identical
        call used at initial connect, see ``_complete_meta``), and only works
        while the current token is *still valid*. A generous 7-day renewal
        window (vs. the 60-second one used for Google/LinkedIn's short-lived
        tokens) means a brief outage of this renewal step doesn't strand the
        integration before the next sync gets another chance.
        """
        now = datetime.now(UTC)
        expires = integration.token_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        current = self.cipher.decrypt(integration.access_token_encrypted)
        fresh = expires is None or expires > now + timedelta(days=7)
        if fresh:
            return current
        try:
            renewed = await self.meta_oauth.exchange_long_lived(current)
        except AppError:
            logger.warning(
                "Meta token renewal failed for integration %s — using the "
                "still-valid stored token for this sync.",
                integration.id,
                exc_info=True,
            )
            return current
        token = renewed.get("access_token")
        if not token:  # renewal failed — fall back to the stored token
            return current
        integration.access_token_encrypted = self.cipher.encrypt(token)
        integration.token_expires_at = self._expiry(renewed.get("expires_in"))
        self.db.commit()
        return token

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
    def ghl_oauth(self) -> GhlOAuthClient:
        if self._ghl_oauth is None:
            self._ghl_oauth = GhlOAuthClient()
        return self._ghl_oauth

    @property
    def ghl_client(self) -> GhlClient:
        if self._ghl_client is None:
            self._ghl_client = GhlClient()
        return self._ghl_client

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
