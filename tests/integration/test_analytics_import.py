"""Tests for CSV analytics import (push reporting data via CSV)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API
from tests.helpers import onboarding_payload

_CSV = (
    "date,platform,impressions,clicks,conversions,leads,spend,revenue\n"
    "2026-07-01,facebook,1000,50,3,10,200,900\n"
    "2026-07-01,google,500,20,1,4,120,300\n"
    "2026-07-01,notaplatform,1,1,1,1,1,1\n"  # invalid platform → skipped
)


def _client_id(client, admin_headers, name="Acme Co."):
    resp = client.post(
        f"{API}/clients/onboarding", headers=admin_headers, json=onboarding_payload(name=name)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client"]["id"]


def _upload(client, headers, cid, csv_text=_CSV):
    return client.post(
        f"{API}/clients/{cid}/analytics/import",
        headers=headers,
        files={"file": ("facts.csv", csv_text.encode(), "text/csv")},
    )


def test_import_valid_rows_and_skips_invalid(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = _upload(client, admin_headers, cid)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["upserted"] == 2  # facebook + google
    assert data["skipped"] == 1  # the bad platform row
    assert data["errors"]

    # The imported facts are now queryable and drive the summary.
    summary = client.get(f"{API}/clients/{cid}/analytics/summary", headers=admin_headers).json()
    assert summary["totals"]["impressions"] == 1500
    assert summary["totals"]["spend"] == 320.0


def test_import_reupload_upserts_not_duplicates(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    _upload(client, admin_headers, cid)
    _upload(client, admin_headers, cid)  # same day/platform → overwrite, not duplicate
    daily = client.get(f"{API}/clients/{cid}/analytics/daily", headers=admin_headers).json()
    assert daily["total"] == 2  # still just facebook + google


def test_import_bad_header_400(client: TestClient, admin_headers: dict):
    cid = _client_id(client, admin_headers)
    resp = _upload(client, admin_headers, cid, "foo,bar\n1,2\n")
    assert resp.status_code == 400


def test_import_scoping_and_auth(client: TestClient, admin_headers: dict, make_user):
    cid = _client_id(client, admin_headers)
    _user, headers = make_user(email="nobody@test.com")
    assert _upload(client, headers, cid).status_code == 404  # unassigned → not found
    assert _upload(client, {}, cid).status_code == 401
