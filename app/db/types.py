"""Shared column types and helpers used across all models.

Keeping these in one place means every model spells UUID / timestamptz / enum
columns the same way.
"""

from __future__ import annotations

import enum

from sqlalchemy import JSON, TIMESTAMP, Float, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

# UUID exposed to Python as ``uuid.UUID``. Renders as native ``UUID`` on
# Postgres and ``CHAR(32)`` elsewhere (e.g. SQLite in tests) — portable.
GUID = Uuid(as_uuid=True)

# Timezone-aware timestamp (``timestamptz`` on Postgres).
TZDateTime = TIMESTAMP(timezone=True)

# JSON column: ``JSONB`` on Postgres, generic ``JSON`` on other backends.
# ``none_as_null=True`` so a Python ``None`` is stored as SQL ``NULL`` (not the
# JSON literal ``null``) — otherwise "absent" rows are hard to filter, e.g.
# ``changes IS NULL`` wouldn't match a JSON-``null`` row (audit-log gotcha).
JSONColumn = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")

# One shared SAEnum instance per enum name. Native Postgres enums are created
# by name, so every column that uses a given enum must reference the SAME type
# instance — otherwise Postgres tries to CREATE TYPE more than once.
_ENUM_CACHE: dict[str, SAEnum] = {}


def pg_enum(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    """Return a cached native Postgres enum that stores the enum *value*."""
    if name not in _ENUM_CACHE:
        _ENUM_CACHE[name] = SAEnum(
            enum_cls,
            name=name,
            native_enum=True,
            values_callable=lambda members: [m.value for m in members],
        )
    return _ENUM_CACHE[name]


class Embedding(TypeDecorator):
    """A vector-embedding column, portable across dialects.

    On Postgres it renders as pgvector's native ``vector(dim)`` (enabling
    ``<=>`` similarity search + ANN indexes) when ``pgvector`` is installed;
    everywhere else (and if pgvector is absent) it degrades to a JSON array of
    floats, so tests run hermetically on SQLite and similarity is computed in
    Python. Python value is always ``list[float] | None``.
    """

    impl = JSON
    cache_ok = True

    class Comparator(TypeDecorator.Comparator):
        """Expose pgvector's distance operators on this column.

        A ``TypeDecorator`` doesn't proxy the pgvector ``Vector`` comparator, so
        ``column.cosine_distance(...)`` would ``AttributeError``. We surface the
        native operators (``<=>`` cosine, ``<->`` L2, ``<#>`` inner product)
        directly — only ever used on Postgres (SQLite search is done in Python).
        """

        def cosine_distance(self, other):
            return self.op("<=>", return_type=Float())(other)

        def l2_distance(self, other):
            return self.op("<->", return_type=Float())(other)

        def max_inner_product(self, other):
            return self.op("<#>", return_type=Float())(other)

    comparator_factory = Comparator

    def __init__(self, dim: int, **kwargs) -> None:
        self.dim = dim
        super().__init__(**kwargs)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector

                return dialect.type_descriptor(Vector(self.dim))
            except ImportError:  # pragma: no cover - pgvector optional
                return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return [float(x) for x in value]
