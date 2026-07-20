"""API tests: cross-client "what's on you" view (BE-04)."""

from __future__ import annotations

import uuid
from datetime import date, time

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.enums import EventType, SocialPlatform
from app.models.event import MarketingEvent
from app.models.plan import PlanTask
from app.models.user import User
from tests.conftest import API
from tests.helpers import onboarding_payload


def _cid(client: TestClient, admin_headers: dict, name: str) -> str:
    r = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert r.status_code == 201, r.text
    return r.json()["client"]["id"]


def _admin_id(db_session: Session) -> uuid.UUID:
    return db_session.query(User).filter(User.email == "admin@test.com").one().id


def _seed(db_session, cid: str, *, assignee: uuid.UUID | None):
    c = uuid.UUID(cid)
    if assignee is not None:
        db_session.add(PlanTask(client_id=c, title="Do a thing", assignee_id=assignee))
    db_session.add(
        MarketingEvent(
            client_id=c,
            title="Draft post",
            type=EventType.content,
            platform=SocialPlatform.instagram,
            event_date=date.today(),
            event_time=time(9, 0),
        )
    )
    db_session.add(Alert(client_id=c, title="CPL over target"))
    db_session.commit()


def test_admin_sees_pending_across_clients(client, admin_headers, db_session):
    admin_id = _admin_id(db_session)
    cid1 = _cid(client, admin_headers, "Client One")
    cid2 = _cid(client, admin_headers, "Client Two")
    _seed(db_session, cid1, assignee=admin_id)
    _seed(db_session, cid2, assignee=None)

    r = client.get(f"{API}/me/pending", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2  # both clients have outstanding items
    by_id = {row["client_id"]: row for row in body["items"]}
    assert by_id[cid1]["assigned_tasks"] == 1
    assert by_id[cid1]["pending_approvals"] == 1
    assert by_id[cid1]["open_alerts"] == 1
    assert by_id[cid1]["total"] == 3
    # cid2 has no task assigned to the admin.
    assert by_id[cid2]["assigned_tasks"] == 0
    assert by_id[cid2]["total"] == 2
    # Grand totals across every accessible client.
    assert body["totals"]["assigned_tasks"] == 1
    assert body["totals"]["total"] == 5


def test_non_admin_only_sees_assigned_clients(client, admin_headers, make_user, db_session):
    user, uh = make_user()
    uid = uuid.UUID(user["id"])
    cid_assigned = _cid(client, admin_headers, "Assigned Co")
    cid_other = _cid(client, admin_headers, "Other Co")

    client.post(
        f"{API}/clients/{cid_assigned}/assignments",
        headers=admin_headers,
        json={"user_id": user["id"]},
    )
    _seed(db_session, cid_assigned, assignee=uid)
    _seed(db_session, cid_other, assignee=uid)  # not assigned → must be hidden

    r = client.get(f"{API}/me/pending", headers=uh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["client_id"] == cid_assigned
    assert body["items"][0]["total"] == 3


def test_empty_when_nothing_pending(client, admin_headers):
    _cid(client, admin_headers, "Quiet Co")  # no seeded items
    r = client.get(f"{API}/me/pending", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["totals"]["total"] == 0


def test_me_pending_requires_auth_401(client):
    assert client.get(f"{API}/me/pending").status_code == 401


def test_me_pending_pagination_422_on_bad_page(client, admin_headers):
    r = client.get(f"{API}/me/pending?page=0", headers=admin_headers)
    assert r.status_code == 422
