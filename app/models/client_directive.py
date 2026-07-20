"""Prioritized client rule store — the enforceable heart of the RAG layer.

Unlike RAG chunks (retrieved by similarity), directives are the deterministic
rules injected *in full* into every downstream agent and compiled into
capability flags. "I don't want AI-generated text" lives here as a mandatory
``must_not`` with ``capability_flags={"ai_text_generation": false}``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    GUID,
    Base,
    CreatedAtMixin,
    JSONColumn,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import DirectiveStatus, DirectiveTier, DirectiveType

if TYPE_CHECKING:
    from app.models.client_profile import ClientProfile


class ClientDirective(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "client_directives"
    __table_args__ = (Index("ix_client_directives_client_tier", "client_id", "tier"),)

    profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("client_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DirectiveType.prefer.value
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DirectiveTier.preference.value
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DirectiveStatus.active.value, index=True
    )
    # Deterministic toggles this directive contributes, e.g. {"ai_text_generation": false}.
    capability_flags: Mapped[dict | None] = mapped_column(JSONColumn)
    # Provenance: the knowledge source this rule came from (SET NULL on delete).
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("knowledge_sources.id", ondelete="SET NULL")
    )
    # Self-reference to a conflicting directive (no FK to keep it decoupled).
    conflicts_with_id: Mapped[uuid.UUID | None] = mapped_column(GUID)

    profile: Mapped[ClientProfile] = relationship(back_populates="directives")
