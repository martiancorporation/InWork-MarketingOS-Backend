"""Data access for knowledge sources + embedded chunks (client RAG layer).

Chunk similarity search is dialect-aware: pgvector's ``<=>`` cosine operator on
Postgres, an in-Python cosine fallback on SQLite (tests). Every query is
hard-filtered by ``client_id`` for tenant isolation.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.repositories.base import BaseRepository


class KnowledgeSourceRepository(BaseRepository[KnowledgeSource]):
    model = KnowledgeSource

    def list_for_client(self, client_id: uuid.UUID) -> list[KnowledgeSource]:
        return list(
            self.db.scalars(
                select(KnowledgeSource).where(KnowledgeSource.client_id == client_id)
            ).all()
        )


class KnowledgeChunkRepository(BaseRepository[KnowledgeChunk]):
    model = KnowledgeChunk

    def delete_for_source(self, source_id: uuid.UUID) -> None:
        self.db.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source_id)
        )

    def search(
        self, client_id: uuid.UUID, query_vec: list[float], top_k: int
    ) -> list[tuple[KnowledgeChunk, float]]:
        """Return the top-k most similar chunks for a client as (chunk, score)."""
        if self.db.bind is not None and self.db.bind.dialect.name == "postgresql":
            return self._search_pg(client_id, query_vec, top_k)
        return self._search_python(client_id, query_vec, top_k)

    def _search_pg(
        self, client_id: uuid.UUID, query_vec: list[float], top_k: int
    ) -> list[tuple[KnowledgeChunk, float]]:
        distance = KnowledgeChunk.embedding.cosine_distance(query_vec)
        rows = self.db.execute(
            select(KnowledgeChunk, distance.label("distance"))
            .where(
                KnowledgeChunk.client_id == client_id,
                KnowledgeChunk.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(top_k)
        ).all()
        return [(row[0], 1.0 - float(row[1])) for row in rows]

    def _search_python(
        self, client_id: uuid.UUID, query_vec: list[float], top_k: int
    ) -> list[tuple[KnowledgeChunk, float]]:
        chunks = self.db.scalars(
            select(KnowledgeChunk).where(
                KnowledgeChunk.client_id == client_id,
                KnowledgeChunk.embedding.is_not(None),
            )
        ).all()
        scored = [
            (c, _cosine(query_vec, c.embedding) * float(c.weight or 1.0))
            for c in chunks
            if c.embedding
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
