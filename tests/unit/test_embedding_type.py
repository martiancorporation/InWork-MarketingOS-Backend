"""Regression: the Embedding column exposes pgvector's distance operators.

Before the fix, ``KnowledgeChunk.embedding.cosine_distance(vec)`` raised
``AttributeError`` (the ``TypeDecorator`` didn't proxy pgvector's comparator), so
RAG vector search was broken on Postgres — invisible to the SQLite suite, which
uses the in-Python fallback. These tests compile the expressions against the
Postgres dialect and assert the native operators render.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.knowledge import KnowledgeChunk

_VEC = [0.1] * 1024


def _compiled(expr) -> str:
    return str(
        select(KnowledgeChunk.id).order_by(expr).compile(dialect=postgresql.dialect())
    )


def test_cosine_distance_compiles_to_pg_operator():
    assert "<=>" in _compiled(KnowledgeChunk.embedding.cosine_distance(_VEC))


def test_l2_distance_compiles_to_pg_operator():
    assert "<->" in _compiled(KnowledgeChunk.embedding.l2_distance(_VEC))


def test_max_inner_product_compiles_to_pg_operator():
    assert "<#>" in _compiled(KnowledgeChunk.embedding.max_inner_product(_VEC))
