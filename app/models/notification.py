"""Per-user notifications — the notification centre / "red dot" and pending-work
surface. A notification belongs to one user (the recipient) and optionally links
to the client it concerns. ``rec_key`` deduplicates recurring signals (e.g. a
watchdog sweep) so repeats update the existing unread notification instead of
piling up. Enum-ish fields are plain indexed strings (open set).

``NotificationPreference`` is the per-user settings row consulted by
``NotificationEmailService`` — email delivery is a separate, decoupled sweep
(see ``app/services/notification_email_service.py``), not something `notify()`
does inline, so it carries zero risk to any of `notify()`'s existing callers.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    GUID,
    Base,
    CreatedAtMixin,
    JSONColumn,
    TimestampMixin,
    TZDateTime,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import NotificationLevel


class Notification(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "read_at"),
        Index("ix_notifications_user_reckey", "user_id", "rec_key"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="info")
    level: Mapped[str] = mapped_column(
        String(8), nullable=False, default=NotificationLevel.info.value
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(512))  # deep-link path for the UI
    rec_key: Mapped[str | None] = mapped_column(String(120))
    read_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    # Set by NotificationEmailService once this row has been included in a
    # digest email — null means "not yet emailed" (or never eligible).
    emailed_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class NotificationPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # Opt-in: a user gets no notification email until they turn this on
    # themselves — no existing user starts receiving mail they never agreed to.
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Client ids this user has muted — no digest *email* for signals about
    # them (the in-app notification/red-dot is unaffected; `notify()` itself
    # never reads this). Stored as a JSON list of strings, not a join table —
    # small, per-user, never queried by client_id from the other direction.
    muted_client_ids: Mapped[list[str]] = mapped_column(JSONColumn, nullable=False, default=list)
