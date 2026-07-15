"""Per-client third-party integration connections (GA4, Meta, LinkedIn, …).

OAuth tokens are stored encrypted at rest. One connection per (client, key).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    GUID,
    Base,
    TimestampMixin,
    TZDateTime,
    UUIDPrimaryKeyMixin,
    pg_enum,
)
from app.models.enums import IntegrationKey, IntegrationStatus

if TYPE_CHECKING:
    from app.models.client import Client


class Integration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("client_id", "key"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[IntegrationKey] = mapped_column(
        pg_enum(IntegrationKey, "integration_key"), nullable=False
    )
    status: Mapped[IntegrationStatus] = mapped_column(
        pg_enum(IntegrationStatus, "integration_status"),
        nullable=False,
        default=IntegrationStatus.disconnected,
        index=True,
    )
    account_label: Mapped[str | None] = mapped_column(String(200))
    external_account_id: Mapped[str | None] = mapped_column(String(160))
    scopes: Mapped[str | None] = mapped_column(Text)  # comma-separated OAuth scopes
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_sync_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)

    client: Mapped[Client] = relationship(back_populates="integrations")
