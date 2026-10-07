"""API tests: admin AI model-routing endpoints (app/api/v1/routers/ai_model_routes.py).

The model catalog these endpoints validate/serve against is the fake, fixed
list installed by the autouse ``_fake_model_catalog`` fixture in
tests/conftest.py — real OpenRouter models are never queried in this suite.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.model_router import ALL_CATEGORIES, AiTaskCategory, bootstrap_model_id
from tests.conftest import API


def test_list_routes_self_heals_every_category(
    client: TestClient, admin_headers: dict, db_session: Session
):
    resp = client.get(f"{API}/admin/ai-model-routes", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    categories = {r["task_category"] for r in items}
    assert categories == set(ALL_CATEGORIES)
    by_category = {r["task_category"]: r for r in items}
    assert by_category[AiTaskCategory.ANALYSIS]["model_id"] == bootstrap_model_id()
    assert by_category[AiTaskCategory.ANALYSIS]["is_active"] is True


def test_list_routes_requires_admin(client: TestClient, make_user):
    _, user_headers = make_user()
    resp = client.get(f"{API}/admin/ai-model-routes", headers=user_headers)
    assert resp.status_code == 403


def test_available_models_lists_the_live_catalog(client: TestClient, admin_headers: dict):
    resp = client.get(f"{API}/admin/ai-model-routes/available-models", headers=admin_headers)
    assert resp.status_code == 200
    ids = {m["model_id"] for m in resp.json()["items"]}
    assert "test-vendor/cheap-fast" in ids
    assert "test-vendor/mid-tier" in ids
    assert "test-vendor/flagship" in ids


def test_update_route_changes_the_model(
    client: TestClient, admin_headers: dict, db_session: Session
):
    resp = client.put(
        f"{API}/admin/ai-model-routes/{AiTaskCategory.CLASSIFICATION}",
        headers=admin_headers,
        json={"model_id": "test-vendor/flagship", "is_active": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"] == "test-vendor/flagship"
    assert body["updated_by"] is not None

    # Persisted — a second GET reflects the change, not the bootstrap default.
    listed = client.get(f"{API}/admin/ai-model-routes", headers=admin_headers).json()["items"]
    row = next(r for r in listed if r["task_category"] == AiTaskCategory.CLASSIFICATION)
    assert row["model_id"] == "test-vendor/flagship"


def test_update_route_rejects_model_not_in_the_live_catalog(
    client: TestClient, admin_headers: dict
):
    resp = client.put(
        f"{API}/admin/ai-model-routes/{AiTaskCategory.CLASSIFICATION}",
        headers=admin_headers,
        json={"model_id": "some-vendor/totally-made-up-model"},
    )
    assert resp.status_code == 400


def test_update_route_rejects_unknown_category(client: TestClient, admin_headers: dict):
    resp = client.put(
        f"{API}/admin/ai-model-routes/not-a-real-category",
        headers=admin_headers,
        json={"model_id": "test-vendor/cheap-fast"},
    )
    assert resp.status_code == 404


def test_update_route_requires_admin(client: TestClient, make_user):
    _, user_headers = make_user()
    resp = client.put(
        f"{API}/admin/ai-model-routes/{AiTaskCategory.CLASSIFICATION}",
        headers=user_headers,
        json={"model_id": "test-vendor/cheap-fast"},
    )
    assert resp.status_code == 403


def test_update_route_rejects_unknown_fields(client: TestClient, admin_headers: dict):
    resp = client.put(
        f"{API}/admin/ai-model-routes/{AiTaskCategory.CLASSIFICATION}",
        headers=admin_headers,
        json={"model_id": "test-vendor/cheap-fast", "not_a_real_field": 1},
    )
    assert resp.status_code == 422


def test_ai_model_routes_require_auth(client: TestClient):
    assert client.get(f"{API}/admin/ai-model-routes").status_code == 401
