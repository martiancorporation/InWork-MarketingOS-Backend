"""Shared column types and helpers used across all models.

Keeping these in one place means every model spells UUID / timestamptz / enum
columns the same way.
"""

from __future__ import annotations

import enum
from typing import Type

from sqlalchemy import TIMESTAMP
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID

# Native Postgres UUID, exposed to Python as ``uuid.UUID``.
GUID = UUID(as_uuid=True)

# Timezone-aware timestamp (stored as ``timestamptz``).
TZDateTime = TIMESTAMP(timezone=True)

# One shared SAEnum instance per enum name. Native Postgres enums are created
# by name, so every column that uses a given enum must reference the SAME type
# instance — otherwise Postgres tries to CREATE TYPE more than once.
_ENUM_CACHE: dict[str, SAEnum] = {}


def pg_enum(enum_cls: Type[enum.Enum], name: str) -> SAEnum:
    """Return a cached native Postgres enum that stores the enum *value*."""
    if name not in _ENUM_CACHE:
        _ENUM_CACHE[name] = SAEnum(
            enum_cls,
            name=name,
            native_enum=True,
            values_callable=lambda members: [m.value for m in members],
        )
    return _ENUM_CACHE[name]
