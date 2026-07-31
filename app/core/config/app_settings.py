"""Application/server settings (name, environment, API prefix)."""

from __future__ import annotations

import ipaddress

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES, env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_name: str = "InWork MarketingOS API"
    app_env: str = "local"  # local | development | production
    # Off by default — flip on only for local debugging. A production deployment
    # must never boot with this true (verbose logs, more detailed error output).
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8000
    # When on, every API request is recorded to the audit_log table by the
    # AuditMiddleware. Disabled in the hermetic test suite (see conftest).
    audit_enabled: bool = True
    # When on, every AI provider call is recorded to ai_usage_events (tokens +
    # cost). Disabled in the hermetic test suite (recorder uses a real session).
    ai_usage_enabled: bool = True
    # In-process rate limiting on sensitive routes (login, paid-AI). Disabled in
    # the test suite so repeated logins don't trip it. NOTE: limits are
    # per-process — with multiple workers, use a shared store (Redis) for exact
    # global limits; this is a per-worker first line of defense.
    rate_limit_enabled: bool = True
    # Comma-separated CIDR blocks of proxies/load-balancers allowed to set
    # X-Forwarded-For (e.g. your ALB/nginx subnet). Empty (the default) means no
    # hop is trusted, so the rate limiter always keys on the raw socket peer —
    # otherwise any client could spoof the header and dodge the limit entirely.
    # Only set this to the actual proxy CIDR(s) in front of the app.
    trusted_proxy_cidrs: str = ""  # TRUSTED_PROXY_CIDRS
    # Hard ceiling on any request body, enforced before routing/parsing —
    # defense in depth so a client can't force the app to buffer an arbitrarily
    # large body in memory just by omitting/lying about Content-Length.
    # Comfortably above the largest legitimate body (file uploads, capped at
    # STORAGE_MAX_UPLOAD_BYTES — 20 MB by default) to leave headroom for
    # multipart encoding overhead.
    max_request_body_bytes: int = 25 * 1024 * 1024  # MAX_REQUEST_BODY_BYTES

    # Absolute base URL (scheme + host + api prefix) this backend is publicly
    # reachable at, e.g. "https://backendai.startwithmartian.com/api/v1". Used to
    # build permanent, signed upload-download links (see app/utils/download_link.py)
    # that stay stable regardless of proxy/Host-header quirks. Not derived from
    # `request.base_url` on purpose. None only in local/dev where such links aren't
    # exercised end-to-end.
    public_api_base_url: str | None = None  # PUBLIC_API_BASE_URL

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        nets = []
        for raw in self.trusted_proxy_cidrs.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                nets.append(ipaddress.ip_network(raw, strict=False))
            except ValueError:
                continue
        return nets

    def is_trusted_proxy(self, ip: str) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self.trusted_proxy_networks)
