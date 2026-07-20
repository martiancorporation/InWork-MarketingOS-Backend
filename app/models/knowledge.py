"""Per-client knowledge layer: ingested sources + embedded RAG chunks.

``KnowledgeSource`` tracks provenance and a ``content_hash`` so incremental
rebuilds skip unchanged inputs. ``KnowledgeChunk`` holds the vector index; every
retrieval hard-filters by ``client_id`` for tenant isolation.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    GUID,
    Base,
    CreatedAtMixin,
    Embedding,
    JSONColumn,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import SourceStatus

if TYPE_CHECKING:
    pass

# Embedding dimensionality. Must match INTEL_EMBEDDING_DIM / the embedding model
# (voyage-3 = 1024). Changing this requires a migration that rewrites the column.
EMBEDDING_DIM = 1024


class KnowledgeSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_sources"

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    # Polymorphic pointer to the origin (no hard FK — resolved in the service).
    ref_kind: Mapped[str | None] = mapped_column(String(40))  # upload | document | field
    ref_id: Mapped[uuid.UUID | None] = mapped_column(GUID)
    ref_key: Mapped[str | None] = mapped_column(String(120), index=True)  # e.g. "brand"
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SourceStatus.pending.value, index=True
    )
    error: Mapped[str | None] = mapped_column(Text)

    chunks: Mapped[list[KnowledgeChunk]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class KnowledgeChunk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_chunks"

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[list[float] | None] = mapped_column(Embedding(EMBEDDING_DIM))
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    meta: Mapped[dict | None] = mapped_column(JSONColumn)

    source: Mapped[KnowledgeSource] = relationship(back_populates="chunks")
