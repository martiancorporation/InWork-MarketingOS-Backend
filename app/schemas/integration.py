"""Per-client integration-connection schemas.

Mirrors the web integrations page: a small fixed catalog of connectors (GA4,
Search Console, Google Ads, Google LSA, Meta, LinkedIn) that a client can
connect or disconnect. Phase-1 OAuth is *simulated* — no real tokens are stored
— so these schemas never expose the ``*_encrypted`` token columns.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import IntegrationKey, IntegrationStatus
from app.schemas.common import MAX_LONG_LINE, ORMModel, StrictModel


class OAuthStartResponse(BaseModel):
    """Where to send the client to authorize + the signed CSRF ``state`` to echo back."""

    authorization_url: str
    state: str


class OAuthCompleteRequest(StrictModel):
    """The ``code`` + ``state`` the provider redirected back with (via the SPA)."""

    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=1024)


class IntegrationConnectRequest(BaseModel):
    """Simulated-connect body — the account the connector is bound to.

    Optional: the frontend may connect a bare connector (status flips to
    ``connected``) or supply the picked account's label / external id / scopes.
    """

    account_label: str | None = Field(None, max_length=200)
    external_account_id: str | None = Field(None, max_length=160)
    scopes: str | None = Field(None, max_length=MAX_LONG_LINE)  # comma-separated


class IntegrationRead(ORMModel):
    """A connector's state — the stored row, or a synthesized disconnected view.

    Token columns (``*_encrypted``, ``token_expires_at``) are deliberately never
    surfaced.
    """

    id: uuid.UUID
    client_id: uuid.UUID
    key: IntegrationKey
    status: IntegrationStatus
    account_label: str | None = None
    external_account_id: str | None = None
    scopes: str | None = None
    last_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class IntegrationListResponse(BaseModel):
    """The full connector catalog for a client (small, fixed — no pagination)."""

    items: list[IntegrationRead]
