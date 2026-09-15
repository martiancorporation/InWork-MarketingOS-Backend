"""API tests: admin AI model-routing endpoints (app/api/v1/routers/ai_model_routes.py)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.model_router import ALL_CATEGORIES, AiTaskCategory, builtin_default
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
    assert by_category[AiTaskCategory.ANALYSIS]["model_id"] == builtin_default(
        AiTaskCategory.ANALYSIS
    )
    assert by_category[AiTaskCategory.ANALYSIS]["is_active"] is True


def test_list_routes_requires_admin(client: TestClient, make_user):
    _, user_headers = make_user()
    resp = client.get(f"{API}/admin/ai-model-routes", headers=user_headers)
    assert resp.status_code == 403


def test_available_models_lists_the_known_catalog(client: TestClient, admin_headers: dict):
    resp = client.get(f"{API}/admin/ai-model-routes/available-models", headers=admin_headers)
    assert resp.status_code == 200
    ids = {m["model_id"] for m in resp.json()["items"]}
    assert "qwen/qwen3.7-flash" in ids
    assert "openai/gpt-5.6-luna" in ids


def test_update_route_changes_the_model(
    client: TestClient, admin_headers: dict, db_session: Session
):
    resp = client.put(
        f"{API}/admin/ai-model-routes/{AiTaskCategory.CLASSIFICATION}",
        headers=admin_headers,
        json={"model_id": "openai/gpt-5.6-luna", "is_active": True},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"] == "openai/gpt-5.6-luna"
    assert body["updated_by"] is not None

    # Persisted — a second GET reflects the change, not the built-in default.
    listed = client.get(f"{API}/admin/ai-model-routes", headers=admin_headers).json()["items"]
    row = next(r for r in listed if r["task_category"] == AiTaskCategory.CLASSIFICATION)
    assert row["model_id"] == "openai/gpt-5.6-luna"


def test_update_route_rejects_unknown_model(client: TestClient, admin_headers: dict):
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
        json={"model_id": "qwen/qwen3.7-flash"},
    )
    assert resp.status_code == 404


def test_update_route_requires_admin(client: TestClient, make_user):
    _, user_headers = make_user()
    resp = client.put(
        f"{API}/admin/ai-model-routes/{AiTaskCategory.CLASSIFICATION}",
        headers=user_headers,
        json={"model_id": "qwen/qwen3.7-flash"},
    )
    assert resp.status_code == 403


def test_update_route_rejects_unknown_fields(client: TestClient, admin_headers: dict):
    resp = client.put(
        f"{API}/admin/ai-model-routes/{AiTaskCategory.CLASSIFICATION}",
        headers=admin_headers,
        json={"model_id": "qwen/qwen3.7-flash", "not_a_real_field": 1},
    )
    assert resp.status_code == 422


def test_ai_model_routes_require_auth(client: TestClient):
    assert client.get(f"{API}/admin/ai-model-routes").status_code == 401
