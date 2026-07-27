"""Analytics freshness: the "Data as of …" timestamp, staleness, and provenance labels.

Reporting reads only from our own tables, so the UI has to tell the user how old
that data is. ``data_as_of`` is the newest sync across the client's connected
connectors; ``stale`` flags an overdue nightly refresh; ``sources`` lets the UI
label seeded numbers rather than passing them off as live.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.enums import AnalyticsSource, IntegrationKey, IntegrationStatus, SocialPlatform
from app.models.integration import Integration
from app.schemas.analytics import AnalyticsDailyIn
from app.services.analytics_service import AnalyticsService
from tests.conftest import API
from tests.helpers import onboarding_payload

DAY = "2026-09-01"


def _client_id(client: TestClient, admin_headers: dict) -> str:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name="Acme Co.")
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _seed(db: Session, cid: uuid.UUID, source: AnalyticsSource) -> None:
    AnalyticsService(db).ingest(
        cid,
        [AnalyticsDailyIn(date=DAY, platform=SocialPlatform.google, impressions=100, clicks=5)],
        source=source,
    )


def _connect(db: Session, cid: uuid.UUID, *, synced_hours_ago: float | None) -> None:
    last_sync = (
        None if synced_hours_ago is None else datetime.now(UTC) - timedelta(hours=synced_hours_ago)
    )
    db.add(
        Integration(
            client_id=cid,
            key=IntegrationKey.meta,
            status=IntegrationStatus.connected,
            last_sync_at=last_sync,
        )
    )
    db.commit()


def _summary(client: TestClient, headers: dict, cid: str) -> dict:
    resp = client.get(f"{API}/clients/{cid}/analytics/summary", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_synthetic_only_client_has_no_timestamp_and_reads_stale(
    client: TestClient, admin_headers: dict, db_session: Session
):
    """Nothing connected yet — be honest: no refresh has happened, and say it's sample data."""
    cid = _client_id(client, admin_headers)
    _seed(db_session, uuid.UUID(cid), AnalyticsSource.synthetic)

    body = _summary(client, admin_headers, cid)
    assert body["data_as_of"] is None
    assert body["stale"] is True
    assert body["sources"] == ["synthetic"]


def test_recent_sync_reports_fresh(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    _seed(db_session, uuid.UUID(cid), AnalyticsSource.connector)
    _connect(db_session, uuid.UUID(cid), synced_hours_ago=2)

    body = _summary(client, admin_headers, cid)
    assert body["data_as_of"] is not None
    assert body["stale"] is False
    assert body["sources"] == ["connector"]


def test_overdue_sync_reports_stale(
    client: TestClient, admin_headers: dict, db_session: Session
):
    """Nightly cadence + 36h grace: a two-day-old sync means the refresh was missed."""
    cid = _client_id(client, admin_headers)
    _seed(db_session, uuid.UUID(cid), AnalyticsSource.connector)
    _connect(db_session, uuid.UUID(cid), synced_hours_ago=48)

    body = _summary(client, admin_headers, cid)
    assert body["data_as_of"] is not None
    assert body["stale"] is True


def test_disconnected_connector_does_not_count_as_fresh(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = _client_id(client, admin_headers)
    _seed(db_session, uuid.UUID(cid), AnalyticsSource.connector)
    db_session.add(
        Integration(
            client_id=uuid.UUID(cid),
            key=IntegrationKey.meta,
            status=IntegrationStatus.disconnected,
            last_sync_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    body = _summary(client, admin_headers, cid)
    assert body["data_as_of"] is None, "a disconnected connector must not vouch for freshness"
    assert body["stale"] is True


def test_mixed_provenance_is_reported(
    client: TestClient, admin_headers: dict, db_session: Session
):
    """A client part-migrated off seed data shows both, so the UI can caveat precisely."""
    cid = _client_id(client, admin_headers)
    service = AnalyticsService(db_session)
    service.ingest(
        uuid.UUID(cid),
        [AnalyticsDailyIn(date=DAY, platform=SocialPlatform.google, impressions=10)],
        source=AnalyticsSource.connector,
    )
    service.ingest(
        uuid.UUID(cid),
        [AnalyticsDailyIn(date=DAY, platform=SocialPlatform.seo, impressions=10)],
        source=AnalyticsSource.synthetic,
    )

    assert _summary(client, admin_headers, cid)["sources"] == ["connector", "synthetic"]


def test_freshness_is_per_client(
    client: TestClient, admin_headers: dict, db_session: Session
):
    """One client's connected sync must not make another client's data look fresh."""
    fresh = _client_id(client, admin_headers)
    other = uuid.UUID(
        client.post(
            f"{API}/clients/onboarding",
            headers=admin_headers,
            json=onboarding_payload(name="Northwind Labs"),
        ).json()["client"]["id"]
    )
    _seed(db_session, uuid.UUID(fresh), AnalyticsSource.connector)
    _seed(db_session, other, AnalyticsSource.synthetic)
    _connect(db_session, uuid.UUID(fresh), synced_hours_ago=1)

    assert _summary(client, admin_headers, fresh)["stale"] is False
    assert _summary(client, admin_headers, str(other))["data_as_of"] is None
