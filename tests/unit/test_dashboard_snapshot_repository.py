"""``DashboardSnapshotRepository.upsert`` must be a single atomic statement.

The previous get-then-insert-or-update idiom raced: two inserts for the same
client (simulated here by calling upsert twice before either had a chance to
see the other's row) would have the second commit blow up on the unique
constraint on ``client_id``. A real INSERT ... ON CONFLICT DO UPDATE can't hit
that — whichever call lands "second" just overwrites the first.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.client import Client
from app.repositories.dashboard_snapshot_repository import DashboardSnapshotRepository


def _client(db: Session) -> Client:
    c = Client(slug="snapshot-co", name="Snapshot Co")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_upsert_twice_for_the_same_client_does_not_raise(db_session: Session) -> None:
    client = _client(db_session)
    repo = DashboardSnapshotRepository(db_session)

    # Two "concurrent" cache-fill attempts for the same client — this is
    # exactly the shape of two racing requests each finishing their own AI
    # pipeline and trying to write the result.
    repo.upsert(
        client.id,
        payload={"health_score": {"score": 1}},
        inputs_hash="hash-a",
        computed_at=datetime.now(UTC),
    )
    db_session.commit()
    repo.upsert(
        client.id,
        payload={"health_score": {"score": 2}},
        inputs_hash="hash-b",
        computed_at=datetime.now(UTC),
    )
    db_session.commit()

    row = repo.get_for_client(client.id)
    assert row is not None
    assert row.inputs_hash == "hash-b"  # the later write wins
    assert row.payload["health_score"]["score"] == 2


def test_upsert_is_the_only_row_for_the_client(db_session: Session) -> None:
    """No duplicate rows land even when upsert is called before a commit in
    between — the unique constraint on client_id is never violated."""
    client = _client(db_session)
    repo = DashboardSnapshotRepository(db_session)

    repo.upsert(client.id, payload={"a": 1}, inputs_hash="h1", computed_at=datetime.now(UTC))
    repo.upsert(client.id, payload={"a": 2}, inputs_hash="h2", computed_at=datetime.now(UTC))
    db_session.commit()

    row = repo.get_for_client(client.id)
    assert row is not None
    assert row.inputs_hash == "h2"
