"""Generic repository base — thin, typed data-access helpers.

Repositories own persistence only (queries, add/flush). They never commit — the
service layer owns the transaction boundary so multi-step operations stay atomic.
"""

from __future__ import annotations

import uuid
from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, entity_id: str | uuid.UUID) -> ModelT | None:
        return self.db.get(self.model, self._as_uuid(entity_id))

    def add(self, entity: ModelT) -> ModelT:
        self.db.add(entity)
        return entity

    def flush(self) -> None:
        self.db.flush()

    @staticmethod
    def _as_uuid(value: str | uuid.UUID) -> uuid.UUID | None:
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (ValueError, TypeError):
            return None
