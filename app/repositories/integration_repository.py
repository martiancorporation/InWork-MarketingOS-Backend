"""Data access for per-client integration connections.

Every query is hard-filtered by ``client_id`` so a connector can never leak
across clients — the same tenant-isolation stance the rest of the repositories
take. There is at most one row per ``(client_id, key)`` (DB unique constraint).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select

from app.models.enums import IntegrationKey, IntegrationStatus
from app.models.integration import Integration
from app.repositories.base import BaseRepository


class IntegrationRepository(BaseRepository[Integration]):
    model = Integration

    def get_for_client(self, client_id: uuid.UUID, key: IntegrationKey) -> Integration | None:
        """Load the one connector row for a (client, key), scoped to the client."""
        return self.db.scalar(
            select(Integration).where(
                Integration.client_id == client_id,
                Integration.key == key,
            )
        )

    def list_for_client(self, client_id: uuid.UUID) -> list[Integration]:
        """All stored connector rows for a client (the fixed catalog is small)."""
        return list(
            self.db.scalars(
                select(Integration)
                .where(Integration.client_id == client_id)
                .order_by(Integration.key.asc())
            ).all()
        )

    def latest_sync_at(self, client_id: uuid.UUID) -> datetime | None:
        """Newest successful sync across the client's *connected* connectors.

        This is the one headline "data as of" timestamp the dashboard shows —
        analytics is stored per-platform, but the user needs a single answer to
        "how fresh is what I'm looking at". Returns None when nothing is
        connected (e.g. a client still running on seeded data).
        """
        return self.db.scalar(
            select(func.max(Integration.last_sync_at)).where(
                Integration.client_id == client_id,
                Integration.status == IntegrationStatus.connected,
            )
        )
