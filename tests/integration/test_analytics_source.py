"""Provenance on daily facts: a real sync must overwrite synthetic seed data in place.

``source`` sits outside the (client, date, platform) natural key precisely so
seeded demo rows are replaced — not duplicated — the first time a connector runs.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.analytics import AnalyticsDaily
from app.models.client import Client
from app.models.enums import AnalyticsSource, SocialPlatform
from app.schemas.analytics import AnalyticsDailyIn
from app.services.analytics_service import AnalyticsService
from app.services.demo_data_service import DemoDataService
from tests.conftest import API
from tests.helpers import onboarding_payload

DAY = "2026-09-01"


def _client_id(client: TestClient, admin_headers: dict) -> str:
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name="Acme Co.")
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _row(**over) -> AnalyticsDailyIn:
    return AnalyticsDailyIn(
        **{
            "date": DAY,
            "platform": SocialPlatform.google,
            "impressions": 1000,
            "clicks": 50,
            "conversions": 5,
            "leads": 7,
            "spend": 120.0,
            "revenue": 900.0,
            **over,
        }
    )


def _stored(db: Session, cid: uuid.UUID) -> list[AnalyticsDaily]:
    return list(db.scalars(select(AnalyticsDaily).where(AnalyticsDaily.client_id == cid)).all())


def test_connector_sync_overwrites_synthetic_row_in_place(
    client: TestClient, admin_headers: dict, db_session: Session
):
    cid = uuid.UUID(_client_id(client, admin_headers))
    service = AnalyticsService(db_session)

    service.ingest(cid, [_row()], source=AnalyticsSource.synthetic)
    seeded = _stored(db_session, cid)
    assert len(seeded) == 1
    assert seeded[0].source == "synthetic"
    assert seeded[0].impressions == 1000

    # A real sync lands for the same (client, date, platform).
    service.ingest(cid, [_row(impressions=4321, leads=99)], source=AnalyticsSource.connector)

    rows = _stored(db_session, cid)
    assert len(rows) == 1, "the real sync must replace the synthetic cell, not add a second row"
    assert rows[0].source == "connector", "stale synthetic provenance must be cleared"
    assert rows[0].impressions == 4321
    assert rows[0].leads == 99


def test_reseeding_is_idempotent(client: TestClient, admin_headers: dict, db_session: Session):
    cid = uuid.UUID(_client_id(client, admin_headers))
    service = AnalyticsService(db_session)
    rows = [_row(), _row(platform=SocialPlatform.facebook)]

    service.ingest(cid, rows, source=AnalyticsSource.synthetic)
    service.ingest(cid, rows, source=AnalyticsSource.synthetic)

    total = db_session.scalar(
        select(func.count())
        .select_from(AnalyticsDaily)
        .where(AnalyticsDaily.client_id == cid)
    )
    assert total == 2


def test_manual_ingest_defaults_to_connector(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/analytics/ingest",
        headers=admin_headers,
        json={"rows": [{"date": DAY, "platform": "google", "impressions": 10, "clicks": 1}]},
    )
    assert resp.status_code == 200, resp.text

    daily = client.get(f"{API}/clients/{cid}/analytics/daily", headers=admin_headers)
    assert daily.status_code == 200, daily.text
    assert daily.json()["items"][0]["source"] == "connector"


def test_csv_import_is_tagged_csv(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    csv_body = f"date,platform,impressions,clicks,leads\n{DAY},google,500,25,4\n"
    resp = client.post(
        f"{API}/clients/{cid}/analytics/import",
        headers=admin_headers,
        files={"file": ("facts.csv", csv_body, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["upserted"] == 1

    daily = client.get(f"{API}/clients/{cid}/analytics/daily", headers=admin_headers)
    assert daily.json()["items"][0]["source"] == "csv"


def test_seeder_never_overwrites_real_data(
    client: TestClient, admin_headers: dict, db_session: Session
):
    """The seeder must skip cells holding connector/CSV data — measured fact wins.

    Guards the live-database hazard: re-seeding a client that already has real
    analytics would otherwise clobber it and re-tag it ``synthetic``.
    """
    cid = uuid.UUID(_client_id(client, admin_headers))
    service = AnalyticsService(db_session)
    # A real connector row lands first.
    service.ingest(cid, [_row(impressions=7777)], source=AnalyticsSource.connector)

    demo = DemoDataService(db_session)
    protected = demo._protected_cells(cid)
    assert (date.fromisoformat(DAY), SocialPlatform.google.value) in protected

    # Now seed for real: the connector cell must come through untouched.
    demo.seed_client(db_session.get(Client, cid))
    db_session.commit()

    stored = {(r.date, r.platform, r.source): r for r in _stored(db_session, cid)}
    real = stored[(date.fromisoformat(DAY), SocialPlatform.google, "connector")]
    assert real.impressions == 7777, "real measured value must survive a seed"


def test_source_is_not_settable_from_the_request_body(client: TestClient, admin_headers: dict):
    """Provenance is server-determined — a caller must not pass synthetic data off as real."""
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/analytics/ingest",
        headers=admin_headers,
        json={
            "rows": [
                {"date": DAY, "platform": "google", "impressions": 10, "source": "connector"}
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    daily = client.get(f"{API}/clients/{cid}/analytics/daily", headers=admin_headers)
    # Ignored on the way in, and still stamped by the server.
    assert daily.json()["items"][0]["source"] == "connector"
