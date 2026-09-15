"""API tests: AI content-calendar generation (propose/assign/reject/regenerate)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _assign_user(client, admin_headers, cid, uid, caps=None):
    body: dict = {"user_id": uid}
    if caps is not None:
        body["capabilities"] = caps
    resp = client.post(f"{API}/clients/{cid}/assignments", headers=admin_headers, json=body)
    assert resp.status_code == 201, resp.text


def _propose(
    client, headers, cid, prompt="Create a content calendar for this month", month="2026-09"
):
    return client.post(
        f"{API}/clients/{cid}/plan/ai/propose",
        headers=headers,
        json={"prompt": prompt, "month": month},
    )


def _fake_month_complete(items: list[dict]):
    import json

    async def fake_complete(self, *, system, prompt, max_tokens=None, model=None, context=None):
        return json.dumps({"items": items})

    return fake_complete


def test_propose_unconfigured_uses_deterministic_fallback(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = _propose(client, admin_headers, cid)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) > 0
    for item in items:
        assert item["status"] == "todo"
        assert item["priority"] == "medium"
        assert item["event_id"] is None or item["content"] is not None
        assert item["content"]["approval_status"] == "pending"
        assert item["content"]["stage"] == "draft"


def test_propose_uses_ai_when_configured(client: TestClient, admin_headers: dict, monkeypatch):
    from app.integrations.llm.openrouter import OpenRouterClient

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        OpenRouterClient,
        "complete",
        _fake_month_complete(
            [
                {
                    "title": "Carousel: Fall specials",
                    "event_date": "2026-09-10",
                    "platform": "instagram",
                    "content_format": "carousel",
                    "category": "content",
                    "caption": "Check out our fall specials!",
                    "hashtags": "#fall #specials",
                    "suggested_role": "content creator",
                }
            ]
        ),
    )
    cid = _client_id(client, admin_headers)
    resp = _propose(client, admin_headers, cid)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "Carousel: Fall specials"
    assert item["content"]["caption"] == "Check out our fall specials!"
    assert item["content"]["content_format"] == "carousel"
    assert item["content"]["platform"] == "instagram"


def test_propose_invalid_month_rejected(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = _propose(client, admin_headers, cid, month="2026-9")
    assert resp.status_code == 422


def test_propose_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    resp = _propose(client, user_headers, cid)
    assert resp.status_code == 404


def test_propose_requires_manage_calendar_capability(
    client: TestClient, admin_headers: dict, make_user
):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    _assign_user(client, admin_headers, cid, user["id"], caps=["review_results"])
    resp = _propose(client, user_headers, cid)
    assert resp.status_code == 403


def test_assign_approves_event_and_notifies(client: TestClient, admin_headers: dict, make_user):
    user, _user_headers = make_user()
    cid = _client_id(client, admin_headers)
    _assign_user(client, admin_headers, cid, user["id"])
    task = _propose(client, admin_headers, cid).json()["items"][0]

    resp = client.post(
        f"{API}/clients/{cid}/plan/ai/items/{task['id']}/assign",
        headers=admin_headers,
        json={"assignee_id": user["id"], "priority": "high"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assignee_id"] == user["id"]
    assert body["priority"] == "high"
    assert body["status"] == "in_progress"
    assert body["content"]["approval_status"] == "approved"
    assert body["content"]["stage"] == "scheduled"

    notifications = client.get(f"{API}/notifications", headers=_user_headers).json()
    assert any(n["title"].startswith("New task assigned") for n in notifications["items"])


def test_reject_requires_admin(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    _assign_user(client, admin_headers, cid, user["id"], caps=["manage_calendar"])
    task = _propose(client, admin_headers, cid).json()["items"][0]

    resp = client.post(
        f"{API}/clients/{cid}/plan/ai/items/{task['id']}/reject",
        headers=user_headers,
        json={"reason": "not relevant"},
    )
    assert resp.status_code == 403


def test_reject_blocks_task_and_records_reason(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    task = _propose(client, admin_headers, cid).json()["items"][0]

    resp = client.post(
        f"{API}/clients/{cid}/plan/ai/items/{task['id']}/reject",
        headers=admin_headers,
        json={"reason": "client doesn't want this topic"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["content"]["approval_status"] == "rejected"


def test_regenerate_requires_admin(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    _assign_user(client, admin_headers, cid, user["id"], caps=["manage_calendar"])
    task = _propose(client, admin_headers, cid).json()["items"][0]

    resp = client.post(
        f"{API}/clients/{cid}/plan/ai/items/{task['id']}/regenerate",
        headers=user_headers,
        json={"instructions": "make it about car AC repair", "reason": "client changed topic"},
    )
    assert resp.status_code == 403


def test_regenerate_replaces_content_and_resets_to_pending(
    client: TestClient, admin_headers: dict, monkeypatch
):
    from app.integrations.llm.openrouter import OpenRouterClient

    cid = _client_id(client, admin_headers)
    task = _propose(client, admin_headers, cid).json()["items"][0]

    import json

    async def fake_single(self, *, system, prompt, max_tokens=None, model=None, context=None):
        return json.dumps(
            {
                "title": "Reel: Car AC repair tips",
                "platform": "instagram",
                "content_format": "reel",
                "category": "content",
                "caption": "Beat the heat — AC repair tips.",
                "hashtags": "#carcare #ac",
                "suggested_role": "video editor",
            }
        )

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(OpenRouterClient, "complete", fake_single)

    resp = client.post(
        f"{API}/clients/{cid}/plan/ai/items/{task['id']}/regenerate",
        headers=admin_headers,
        json={"instructions": "make it about car AC repair", "reason": "client changed topic"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Reel: Car AC repair tips"
    assert body["status"] == "todo"
    assert body["content"]["caption"] == "Beat the heat — AC repair tips."
    assert body["content"]["approval_status"] == "pending"
    assert body["content"]["stage"] == "draft"


def test_regenerate_without_linked_event_is_bad_request(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    plain_task = client.post(
        f"{API}/clients/{cid}/plan/tasks",
        headers=admin_headers,
        json={"title": "Manual SEO audit", "category": "analytics"},
    ).json()

    resp = client.post(
        f"{API}/clients/{cid}/plan/ai/items/{plain_task['id']}/regenerate",
        headers=admin_headers,
        json={"instructions": "anything", "reason": "test"},
    )
    assert resp.status_code == 400


def test_propose_unknown_field_rejected(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/plan/ai/propose",
        headers=admin_headers,
        json={"prompt": "x", "month": "2026-09", "extra": "nope"},
    )
    assert resp.status_code == 422


def test_propose_empty_prompt_is_accepted(client: TestClient, admin_headers: dict):
    """The AI prompt is optional (BE-request): the client's brand/goals/
    strategy already ground the agent, so a blank prompt must still generate
    a plan rather than 422ing."""
    cid = _client_id(client, admin_headers)
    resp = _propose(client, admin_headers, cid, prompt="")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["items"]) > 0


def test_propose_current_month_never_generates_before_today(
    client: TestClient, admin_headers: dict, monkeypatch
):
    """Regression guard for the past-date bug: generating "this month" must
    never place an item before today, even when the AI itself returns one."""
    from calendar import monthrange
    from datetime import date

    from app.integrations.llm.openrouter import OpenRouterClient

    today = date.today()
    current_month = f"{today.year:04d}-{today.month:02d}"
    last_day = monthrange(today.year, today.month)[1]

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        OpenRouterClient,
        "complete",
        _fake_month_complete(
            [
                # Deliberately dated on the 1st of the month — before "today"
                # on every day of the month except the 1st itself.
                {
                    "title": "Backdated item",
                    "event_date": f"{today.year:04d}-{today.month:02d}-01",
                    "platform": "instagram",
                    "content_format": "static",
                    "category": "content",
                    "caption": "should never land before today",
                    "hashtags": "",
                    "suggested_role": "content creator",
                },
                {
                    "title": "Last day of month item",
                    "event_date": f"{today.year:04d}-{today.month:02d}-{last_day:02d}",
                    "platform": "instagram",
                    "content_format": "static",
                    "category": "content",
                    "caption": "a real future/today date",
                    "hashtags": "",
                    "suggested_role": "content creator",
                },
            ]
        ),
    )
    cid = _client_id(client, admin_headers)
    resp = _propose(client, admin_headers, cid, month=current_month)
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        event_date = date.fromisoformat(item["content"]["event_date"])
        assert event_date >= today, f"{item['title']} was dated {event_date}, before today {today}"


def test_propose_past_month_is_unaffected_by_the_today_floor(
    client: TestClient, admin_headers: dict, monkeypatch
):
    """Explicitly generating an already-past month (e.g. backfilling history)
    must still allow dates throughout that month — the floor only applies
    when the requested month is the *current* one."""
    from app.integrations.llm.openrouter import OpenRouterClient

    monkeypatch.setattr(OpenRouterClient, "is_configured", property(lambda self: True))
    monkeypatch.setattr(
        OpenRouterClient,
        "complete",
        _fake_month_complete(
            [
                {
                    "title": "Early-January item",
                    "event_date": "2020-01-02",
                    "platform": "instagram",
                    "content_format": "static",
                    "category": "content",
                    "caption": "a long-past date, still valid for a past month",
                    "hashtags": "",
                    "suggested_role": "content creator",
                }
            ]
        ),
    )
    cid = _client_id(client, admin_headers)
    resp = _propose(client, admin_headers, cid, month="2020-01")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["content"]["event_date"] == "2020-01-02"
