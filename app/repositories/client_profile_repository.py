"""Data access for versioned client profiles + their directives."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.client_directive import ClientDirective
from app.models.client_profile import ClientProfile
from app.repositories.base import BaseRepository


class ClientProfileRepository(BaseRepository[ClientProfile]):
    model = ClientProfile

    def get_version(self, client_id: uuid.UUID, version: int) -> ClientProfile | None:
        return self.db.scalar(
            select(ClientProfile).where(
                ClientProfile.client_id == client_id,
                ClientProfile.version == version,
            )
        )

    def next_version(self, client_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(ClientProfile.version)).where(
                ClientProfile.client_id == client_id
            )
        )
        return (current or 0) + 1

    def list_versions(self, client_id: uuid.UUID) -> list[ClientProfile]:
        return list(
            self.db.scalars(
                select(ClientProfile)
                .where(ClientProfile.client_id == client_id)
                .order_by(ClientProfile.version.desc())
            ).all()
        )


class ClientDirectiveRepository(BaseRepository[ClientDirective]):
    model = ClientDirective

    def active_for_profile(self, profile_id: uuid.UUID) -> list[ClientDirective]:
        return list(
            self.db.scalars(
                select(ClientDirective)
                .where(ClientDirective.profile_id == profile_id)
                .order_by(ClientDirective.rank, ClientDirective.created_at)
            ).all()
        )

    def get_owned(
        self, client_id: uuid.UUID, directive_id: uuid.UUID
    ) -> ClientDirective | None:
        return self.db.scalar(
            select(ClientDirective).where(
                ClientDirective.id == directive_id,
                ClientDirective.client_id == client_id,
            )
        )
