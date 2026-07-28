"""Every client — new or backfilled — must arrive with data on both screens.

Until connectors land there is nothing real to report on, so a client created
through the wizard is seeded with a synthetic history. These tests pin the two
things that actually matter: that a *brand-new* client's Dashboard and Analytics
screens have something to render, and that seeding can never overwrite real data.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.analytics import AnalyticsDaily
from app.models.client import Client as ClientModel
from app.models.enums import AnalyticsSource, SocialPlatform
from app.schemas.analytics import AnalyticsDailyIn
from app.services.alert_service import AlertService
from app.services.analytics_service import AnalyticsService
from app.services.demo_data_service import DemoDataService, rows_for_client
from tests.conftest import API
from tests.helpers import onboarding_payload


@pytest.fixture(autouse=True)
def demo_seeding(monkeypatch, db_session: Session):
    """Opt this module back into create-time seeding.

    conftest disables it suite-wide so every other test still sees a genuinely
    empty client; this is the one module that wants the side-effect.

    Seeding normally runs as a background task on its own session — which in a test
    would be a fresh, empty SQLite database, disconnected from the one the requests
    are using. So the *session plumbing* is substituted here (run inline, on the test
    session) while the seeding logic itself stays under test.
    """
    monkeypatch.setattr(get_settings().demo, "seed_on_create", True)

    def _seed_inline(client_id: uuid.UUID, actor_id: uuid.UUID | None = None) -> None:
        client_obj = db_session.get(ClientModel, client_id)
        if client_obj is None:
            return
        result = DemoDataService(db_session).seed_client(
            client_obj, actor_id=actor_id, skip_if_present=True
        )
        if result.skipped:
            return
        db_session.commit()
        AlertService(db_session).evaluate(client_id)

    monkeypatch.setattr(DemoDataService, "seed_detached", staticmethod(_seed_inline))


def _draft(client: TestClient, admin_headers: dict, name="Fresh Co."):
    resp = client.post(
        f"{API}/clients/onboarding/draft",
        headers=admin_headers,
        json={
            "name": name,
            "business_type": "B2B SaaS",
            "industry": "Software",
            "website": "https://fresh.example.com",
            "language": "English",
            "location": "Austin, TX",
            "markets": "United States",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def test_new_draft_client_has_analytics_immediately(client: TestClient, admin_headers: dict):
    """The gap this closes: a fresh client used to open onto empty dashboards."""
    cid = _draft(client, admin_headers)

    summary = client.get(f"{API}/clients/{cid}/analytics/summary", headers=admin_headers)
    assert summary.status_code == 200, summary.text
    totals = summary.json()["totals"]
    assert totals["impressions"] > 0
    assert totals["clicks"] > 0
    assert totals["leads"] > 0
    assert totals["spend"] > 0
    # Derived metrics must be finite, not divide-by-zero artefacts.
    assert totals["ctr"] > 0
    assert totals["cpl"] > 0

    # Multiple channels, so the breakdown charts have more than one bar.
    assert len({row["platform"] for row in summary.json()["by_platform"]}) >= 3
    # And it is honestly labelled as sample data.
    assert summary.json()["sources"] == ["synthetic"]


def test_new_client_populates_every_dashboard_panel(client: TestClient, admin_headers: dict):
    """Analytics + campaigns alone left three panels empty; these are the rest."""
    cid = _draft(client, admin_headers, name="Panels Co.")

    campaigns = client.get(f"{API}/clients/{cid}/campaigns", headers=admin_headers)
    assert campaigns.status_code == 200
    assert campaigns.json()["total"] >= 3
    # KPI targets drive the goal-completion bars.
    assert any(c["target_cpl"] for c in campaigns.json()["items"])

    tasks = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers)
    assert tasks.json()["total"] >= 3, "the calendar widget reads plan tasks"

    events = client.get(
        f"{API}/clients/{cid}/calendar/events",
        headers=admin_headers,
        params={"approval_status": "pending"},
    )
    assert events.json()["total"] >= 1, "Tasks Pending reads calendar items awaiting approval"

    alerts = client.get(f"{API}/clients/{cid}/alerts", headers=admin_headers)
    assert alerts.status_code == 200
    assert alerts.json()["total"] >= 1, "alerts are derived from the seeded campaigns"


def test_plan_tasks_span_a_range_and_vary_in_status(client: TestClient, admin_headers: dict):
    """The calendar is only worth looking at if the colours differ."""
    cid = _draft(client, admin_headers, name="Spread Co.")
    items = client.get(
        f"{API}/clients/{cid}/plan/tasks", headers=admin_headers, params={"page_size": 100}
    ).json()["items"]

    assert len({t["status"] for t in items}) >= 3, "want done + overdue + upcoming"
    assert any(t["start_date"] and t["due_date"] and t["start_date"] != t["due_date"] for t in items), (
        "at least one multi-day span, so the calendar draws a bar"
    )
    assert any(t["start_date"] is None and t["due_date"] is None for t in items), (
        "one undated item, so the board's unscheduled counter is exercised"
    )


def test_seeding_is_deterministic_across_clients_with_the_same_slug_seed(
    client: TestClient, admin_headers: dict
):
    """Same slug → same numbers, so a demo looks identical twice."""
    day = date(2026, 7, 1)
    first = rows_for_client("acme-co", 30, day)
    second = rows_for_client("acme-co", 30, day)
    assert [r.impressions for r in first] == [r.impressions for r in second]
    # A different client gets different numbers.
    other = rows_for_client("other-co", 30, day)
    assert [r.impressions for r in first] != [r.impressions for r in other]


def test_seeding_never_overwrites_connector_data(
    client: TestClient, admin_headers: dict, db_session: Session
):
    """Real measured data always wins over placeholder data."""
    cid = uuid.UUID(_draft(client, admin_headers, name="Realdata Co."))
    row = AnalyticsDailyIn(
        date=date.today(),
        platform=SocialPlatform.google,
        impressions=999_999,
        clicks=1,
        conversions=1,
        leads=1,
        spend=1,
        revenue=1,
    )
    AnalyticsService(db_session).ingest(cid, [row], source=AnalyticsSource.connector)

    obj = db_session.get(ClientModel, cid)
    DemoDataService(db_session).seed_client(obj)
    db_session.commit()

    kept = (
        db_session.query(AnalyticsDaily)
        .filter(
            AnalyticsDaily.client_id == cid,
            AnalyticsDaily.platform == SocialPlatform.google,
            AnalyticsDaily.date == row.date,
        )
        .one()
    )
    assert kept.impressions == 999_999, "the connector value must survive re-seeding"
    assert kept.source == "connector"


def test_reseeding_does_not_duplicate(client: TestClient, admin_headers: dict, db_session: Session):
    """Backfilling a client that was already seeded is a no-op, not a doubling."""
    cid = uuid.UUID(_draft(client, admin_headers, name="Twice Co."))
    obj = db_session.get(ClientModel, cid)

    before = client.get(f"{API}/clients/{cid}/analytics/summary", headers=admin_headers).json()
    DemoDataService(db_session).seed_client(obj)
    db_session.commit()
    after = client.get(f"{API}/clients/{cid}/analytics/summary", headers=admin_headers).json()

    assert after["totals"]["impressions"] == before["totals"]["impressions"]
    tasks = client.get(f"{API}/clients/{cid}/plan/tasks", headers=admin_headers).json()["total"]
    campaigns = client.get(f"{API}/clients/{cid}/campaigns", headers=admin_headers).json()["total"]
    assert tasks <= 7 and campaigns <= 3, "titles are the idempotency key"


def test_atomic_onboarding_also_seeds(client: TestClient, admin_headers: dict):
    """Both creation paths must behave the same."""
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name="Atomic Co.")
    )
    assert resp.status_code == 201, resp.text
    cid = resp.json()["client"]["id"]
    summary = client.get(f"{API}/clients/{cid}/analytics/summary", headers=admin_headers)
    assert summary.json()["totals"]["impressions"] > 0


def test_client_rollups_are_populated(
    client: TestClient, admin_headers: dict, db_session: Session
):
    """``spend_total``/``leads_total``/``cpl`` were read in four places, written in none.

    They back the client list, the dashboard's budget card and headline, and the
    cross-client assistant — so every client reported $0 spend and 0 leads however
    much data it had.
    """
    cid = uuid.UUID(_draft(client, admin_headers, name="Rollup Co."))
    obj = db_session.get(ClientModel, cid)
    db_session.refresh(obj)
    assert float(obj.spend_total) > 0
    assert obj.leads_total > 0
    # CPL must be spend/leads, not a stale or invented number.
    assert abs(float(obj.cpl) - float(obj.spend_total) / obj.leads_total) < 0.02

    # And they surface on the client list, which reads these columns.
    listed = client.get(f"{API}/clients", headers=admin_headers).json()["items"]
    row = next(c for c in listed if c["id"] == str(cid))
    assert row["spend"] > 0 and row["leads"] > 0


def test_dashboard_brief_names_top_and_worst_campaign(client: TestClient, admin_headers: dict):
    """Those two cards used to be hardcoded to "Not enough data yet"."""
    cid = _draft(client, admin_headers, name="Ranked Co.")
    brief = client.get(f"{API}/clients/{cid}/dashboard", headers=admin_headers).json()[
        "executive_brief"
    ]
    top, worst = brief["top_campaign"], brief["worst_campaign"]
    assert top["name"] != "Not enough data yet", top
    assert worst["name"] != "Not enough data yet", worst
    assert top["name"] != worst["name"], "the best and worst should not be the same campaign"
    assert "per lead" in top["note"]
    # And the budget card is no longer $0 of $0.
    assert brief["budget"]["spent"] > 0


def test_rollups_ignore_campaigns_with_no_leads(db_session: Session):
    """A campaign that spent money and produced nothing must not rank as the best."""
    from app.services.dashboard_service import _rank_by_cpl

    good = SimpleNamespace(name="Good", spend=100.0, leads=10)  # $10/lead
    barren = SimpleNamespace(name="Barren", spend=500.0, leads=0)  # would look like $0
    assert _rank_by_cpl([good, barren], best=True) == ("Good", 10.0)
    assert _rank_by_cpl([barren], best=True) is None


def test_seeding_can_be_switched_off(client: TestClient, admin_headers: dict, monkeypatch):
    """It's demo scaffolding — once connectors land this flag turns it off."""
    monkeypatch.setattr(get_settings().demo, "seed_on_create", False)

    cid = _draft(client, admin_headers, name="Empty Co.")
    summary = client.get(f"{API}/clients/{cid}/analytics/summary", headers=admin_headers)
    assert summary.status_code == 200
    assert summary.json()["totals"]["impressions"] == 0
