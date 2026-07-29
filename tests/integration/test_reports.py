"""API tests: the report registry (create/list/get/update/delete + RBAC).

``POST`` now generates a real file (CSV/Excel/PDF/JPEG) before recording the
report row — see ``app/services/reports/``. The "visual" (JPEG) format's
Playwright render is monkeypatched here so the hermetic suite doesn't need a
real headless Chromium; the other three formats run their real renderers
(fast, no browser).
"""

from __future__ import annotations

import re
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_storage
from app.main import app
from app.services.reports import generator as report_generator
from tests.conftest import API
from tests.helpers import onboarding_payload

# The upload path needs python-multipart; skip cleanly if it's absent.
multipart = pytest.importorskip("multipart", reason="python-multipart not installed")

_UPLOAD_ID_RE = re.compile(r"/uploads/([0-9a-f-]{36})/download")


class FakeStorage:
    """In-memory ``Storage`` stand-in — real bytes flow through, nothing hits S3."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.is_configured = True

    def upload(self, fileobj, key, content_type):
        self.objects[key] = {"data": fileobj.read(), "content_type": content_type}

    def download(self, key):
        return self.objects[key]["data"]

    def generate_download_url(self, key, expiry_seconds):
        return f"https://s3.example.test/{key}?signed=1"

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture(autouse=True)
def storage() -> Generator[FakeStorage, None, None]:
    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_storage, None)


@pytest.fixture(autouse=True)
def fake_visual_render(monkeypatch):
    """The hermetic suite shouldn't need a real headless browser for every run."""

    async def fake_render(content):
        return b"\xff\xd8\xff\xe0fake-jpeg-bytes"

    monkeypatch.setattr(report_generator, "render_visual", fake_render)


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _report_payload(**overrides):
    payload = {
        "kind": "performance",
        "format": "pdf",
        "title": "September performance",
        "date_from": "2026-09-01",
        "date_to": "2026-09-30",
        "scope": "holistic",
        "channels": ["meta", "google_ads"],
        "sections": ["campaign_performance", "ga_overview"],
        "save_to_outlook_draft": True,
    }
    payload.update(overrides)
    return payload


def _create(client, headers, cid, **overrides):
    resp = client.post(
        f"{API}/clients/{cid}/reports", headers=headers, json=_report_payload(**overrides)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload_id_from(file_url: str) -> str:
    match = _UPLOAD_ID_RE.search(file_url)
    assert match, f"file_url doesn't look like an upload permalink: {file_url}"
    return match.group(1)


def test_create_report(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    body = _create(client, admin_headers, cid)
    assert body["title"] == "September performance"
    assert body["kind"] == "performance"
    assert body["channels"] == ["meta", "google_ads"]
    assert body["sections"] == ["campaign_performance", "ga_overview"]
    assert body["save_to_outlook_draft"] is True
    assert body["created_by"]
    # The real fix: a file is now actually attached, not a silent null.
    assert body["file_url"]
    assert "/uploads/" in body["file_url"] and "/download?sig=" in body["file_url"]


def test_bad_date_range_422(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = client.post(
        f"{API}/clients/{cid}/reports",
        headers=admin_headers,
        json=_report_payload(date_from="2026-09-30", date_to="2026-09-01"),
    )
    assert resp.status_code == 422


def test_list_and_kind_filter(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _create(client, admin_headers, cid, kind="performance", title="Perf")
    _create(client, admin_headers, cid, kind="compliance", title="Compliance")
    assert client.get(f"{API}/clients/{cid}/reports", headers=admin_headers).json()["total"] == 2
    only = client.get(f"{API}/clients/{cid}/reports?kind=compliance", headers=admin_headers).json()
    assert only["total"] == 1 and only["items"][0]["title"] == "Compliance"


def test_get_report(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    rid = _create(client, admin_headers, cid)["id"]
    resp = client.get(f"{API}/clients/{cid}/reports/{rid}", headers=admin_headers)
    assert resp.status_code == 200 and resp.json()["id"] == rid


def test_update_attaches_file(client: TestClient, admin_headers: dict):
    """The manual-override escape hatch — PATCH still takes an arbitrary raw
    string verbatim, unrelated to the generated-file flow on create."""
    cid = _client_id(client, admin_headers)
    rid = _create(client, admin_headers, cid)["id"]
    resp = client.patch(
        f"{API}/clients/{cid}/reports/{rid}",
        headers=admin_headers,
        json={"file_url": "s3://bucket/report.pdf", "save_to_outlook_draft": False},
    )
    assert resp.status_code == 200
    assert resp.json()["file_url"] == "s3://bucket/report.pdf"
    assert resp.json()["save_to_outlook_draft"] is False


def test_delete_report(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    rid = _create(client, admin_headers, cid)["id"]
    assert (
        client.delete(f"{API}/clients/{cid}/reports/{rid}", headers=admin_headers).status_code
        == 200
    )
    assert (
        client.get(f"{API}/clients/{cid}/reports/{rid}", headers=admin_headers).status_code == 404
    )


def test_reports_are_client_scoped(client: TestClient, admin_headers: dict):
    cid_a = _client_id(client, admin_headers, name="Client A")
    cid_b = _client_id(client, admin_headers, name="Client B")
    rid = _create(client, admin_headers, cid_a)["id"]
    assert (
        client.get(f"{API}/clients/{cid_b}/reports/{rid}", headers=admin_headers).status_code == 404
    )


def test_assigned_user_can_manage_reports(client: TestClient, admin_headers: dict, make_user):
    user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    client.post(
        f"{API}/clients/{cid}/assignments", headers=admin_headers, json={"user_id": user["id"]}
    )
    body = _create(client, user_headers, cid)
    assert body["created_by"] == user["id"]


def test_unassigned_user_gets_404(client: TestClient, admin_headers: dict, make_user):
    _user, user_headers = make_user()
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/reports", headers=user_headers).status_code == 404


def test_reports_require_auth(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    assert client.get(f"{API}/clients/{cid}/reports").status_code == 401


# ---- real file generation, one per format ----


@pytest.mark.parametrize(
    "fmt, expected_content_type",
    [
        ("csv", "text/csv"),
        ("excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("pdf", "application/pdf"),
        ("visual", "image/jpeg"),
    ],
)
def test_export_each_format_produces_a_real_file(
    client: TestClient, admin_headers: dict, fmt: str, expected_content_type: str
):
    cid = _client_id(client, admin_headers)
    body = _create(client, admin_headers, cid, format=fmt)
    assert body["format"] == fmt

    upload_id = _upload_id_from(body["file_url"])
    upload = client.get(f"{API}/uploads/{upload_id}", headers=admin_headers)
    assert upload.status_code == 200, upload.text
    assert upload.json()["content_type"] == expected_content_type
    assert upload.json()["size_bytes"] > 0


def test_export_with_no_analytics_or_campaigns_still_succeeds(
    client: TestClient, admin_headers: dict
):
    """A quiet client (no campaigns, no analytics rows) must still get a real
    file — placeholder content, not a 500."""
    cid = _client_id(client, admin_headers, name="Quiet Co.")
    body = _create(client, admin_headers, cid, format="excel")
    assert body["file_url"]
    upload_id = _upload_id_from(body["file_url"])
    upload = client.get(f"{API}/uploads/{upload_id}", headers=admin_headers)
    assert upload.status_code == 200
    assert upload.json()["size_bytes"] > 0


def test_export_unknown_channels_and_sections_falls_back_gracefully(
    client: TestClient, admin_headers: dict
):
    """Garbage/legacy channel or section strings must not 500 — they're just
    ignored (channels) or treated as "show everything" (sections)."""
    cid = _client_id(client, admin_headers)
    body = _create(
        client,
        admin_headers,
        cid,
        channels=["not-a-real-channel"],
        sections=["not-a-real-section"],
    )
    assert body["file_url"]
