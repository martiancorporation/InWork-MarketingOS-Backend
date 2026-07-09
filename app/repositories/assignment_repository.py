"""Data access for client↔user assignments."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.assignment import ClientAssignment
from app.repositories.base import BaseRepository


class AssignmentRepository(BaseRepository[ClientAssignment]):
    model = ClientAssignment

    def get(self, client_id: uuid.UUID, user_id: uuid.UUID) -> ClientAssignment | None:
        return self.db.scalar(
            select(ClientAssignment).where(
                ClientAssignment.client_id == client_id,
                ClientAssignment.user_id == user_id,
            )
        )

    def exists(self, client_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        return self.get(client_id, user_id) is not None

    def list_for_client(self, client_id: uuid.UUID) -> list[ClientAssignment]:
        return list(
            self.db.scalars(
                select(ClientAssignment)
                .where(ClientAssignment.client_id == client_id)
                .order_by(ClientAssignment.created_at.asc())
            ).all()
        )
