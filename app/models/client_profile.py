"""Versioned client intelligence profile (summary + structured knowledge).

Append-only: every build writes a new ``version``. ``clients.current_profile_version``
points at the live one, flipped atomically only on a successful build so readers
always see a consistent snapshot and failed rebuilds never corrupt the profile.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    GUID,
    Base,
    CreatedAtMixin,
    JSONColumn,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import ProfileStatus

if TYPE_CHECKING:
    from app.models.client_directive import ClientDirective


class ClientProfile(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "client_profiles"
    __table_args__ = (UniqueConstraint("client_id", "version", name="uq_client_profile_version"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ProfileStatus.building.value, index=True
    )
    summary_md: Mapped[str | None] = mapped_column(Text)
    # Structured summary: identity, wants, does_not_want, business_goals,
    # expectations, design_preferences, content_requirements, restrictions, brand.
    profile: Mapped[dict | None] = mapped_column(JSONColumn)
    # Deterministic machine-readable toggles derived from mandatory directives,
    # e.g. {"ai_text_generation": false}. Consumed by the orchestration layer.
    capability_flags: Mapped[dict | None] = mapped_column(JSONColumn)
    model: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Map of source_key -> content_hash at build time; used to diff incrementals.
    source_hashes: Mapped[dict | None] = mapped_column(JSONColumn)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )

    directives: Mapped[list[ClientDirective]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
