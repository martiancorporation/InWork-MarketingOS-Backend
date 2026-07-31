"""Data access for the cached AI dashboard bundle (one row per client)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.dashboard_snapshot import DashboardSnapshot
from app.repositories.base import BaseRepository


class DashboardSnapshotRepository(BaseRepository[DashboardSnapshot]):
    model = DashboardSnapshot

    def get_for_client(self, client_id: uuid.UUID) -> DashboardSnapshot | None:
        return self.db.scalar(
            select(DashboardSnapshot).where(DashboardSnapshot.client_id == client_id)
        )

    def upsert(
        self,
        client_id: uuid.UUID,
        *,
        payload: dict,
        inputs_hash: str,
        computed_at: datetime,
    ) -> None:
        """One atomic ``INSERT ... ON CONFLICT DO UPDATE`` on ``client_id``.

        The previous get-then-write-or-insert was racy: two concurrent cache
        misses for the same client (e.g. two browser tabs hitting an expired
        snapshot at once) both insert, and the second commit hits the unique
        constraint on ``client_id`` and raises an unhandled ``IntegrityError``
        (a 500). A single upsert statement can't lose that race — whichever
        write lands second just overwrites the first, which is exactly the
        "pure cache-replace" semantics this table already documents.
        """
        dialect = self.db.bind.dialect.name if self.db.bind is not None else ""
        insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
        stmt = insert_fn(DashboardSnapshot).values(
            client_id=client_id,
            payload=payload,
            inputs_hash=inputs_hash,
            computed_at=computed_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["client_id"],
            set_={
                "payload": stmt.excluded.payload,
                "inputs_hash": stmt.excluded.inputs_hash,
                "computed_at": stmt.excluded.computed_at,
            },
        )
        self.db.execute(stmt)
