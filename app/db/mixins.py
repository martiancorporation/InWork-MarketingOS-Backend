"""Reusable model mixins for primary keys and timestamps."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import GUID, TZDateTime


class UUIDPrimaryKeyMixin:
    """Adds a UUID primary key, generated application-side."""

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    """Adds a single ``created_at`` timestamp (for append-only / immutable rows)."""

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, server_default=func.now(), nullable=False
    )


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at`` timestamps (for mutable rows)."""

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
