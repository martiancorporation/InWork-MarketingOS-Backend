"""The one agency-wide GoHighLevel (GHL) OAuth connection.

This engagement's GHL setup is ONE shared location across every client (see
``Integration.ghl_tags`` — clients are distinguished by contact/opportunity
tags within that single location, not by separate GHL accounts). Storing the
credential once here — instead of an independent encrypted copy inside every
client's own ``Integration`` row, which is how it worked before — is a real
bug fix, not just a tidy-up: GHL rotates the refresh token on every use, so N
copies of "the same" grant silently diverge the moment any one of them
refreshes, and every other copy's next refresh then fails with
``invalid_grant``. One row, refreshed in one place, makes that failure mode
structurally impossible instead of merely unlikely.

Operationally a singleton — see ``GhlAgencyConnectionRepository.get()`` — a
real DB constraint isn't worth the complexity for a table an admin writes to
maybe once a year via the OAuth connect flow.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, TZDateTime, UUIDPrimaryKeyMixin, pg_enum
from app.models.enums import IntegrationStatus


class GhlAgencyConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ghl_agency_connections"

    status: Mapped[IntegrationStatus] = mapped_column(
        pg_enum(IntegrationStatus, "integration_status"),
        nullable=False,
        default=IntegrationStatus.disconnected,
    )
    # GHL's own ids for what got authorized — informational (nothing derives
    # a request path from company_id today; location_id is the one that
    # matters, since every GHL API call needs it).
    company_id: Mapped[str | None] = mapped_column(String(80))
    location_id: Mapped[str | None] = mapped_column(String(80))
    access_token_encrypted: Mapped[str | None] = mapped_column(Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    connected_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
