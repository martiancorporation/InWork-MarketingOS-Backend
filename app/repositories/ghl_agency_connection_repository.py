"""Data access for the one agency-wide GHL connection (see
``app.models.ghl_agency_connection.GhlAgencyConnection`` for why this is a
singleton rather than a per-client row)."""

from __future__ import annotations

from sqlalchemy import select

from app.models.ghl_agency_connection import GhlAgencyConnection
from app.repositories.base import BaseRepository


class GhlAgencyConnectionRepository(BaseRepository[GhlAgencyConnection]):
    model = GhlAgencyConnection

    def get_singleton(self) -> GhlAgencyConnection | None:
        """The one row, if it's ever been created — oldest first, in the
        unexpected event more than one somehow exists. Named distinctly from
        the inherited ``get(id)`` (different shape, not an override)."""
        return self.db.scalar(
            select(GhlAgencyConnection).order_by(GhlAgencyConnection.created_at.asc()).limit(1)
        )
