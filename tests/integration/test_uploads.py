"""Integration tests for the file API (upload / get / delete).

The S3 backend is replaced with an in-memory ``FakeStorage`` via a dependency
override, so nothing touches AWS. One test uses the real (unconfigured)
``S3Storage`` to assert the graceful 503 degradation.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_storage
from app.core.config.storage import StorageSettings
from app.integrations.aws import S3Storage
from app.main import app

API = "/api/v1"

# The proxied upload route needs python-multipart; skip cleanly if it's absent.
multipart = pytest.importorskip("multipart", reason="python-multipart not installed")


class FakeStorage:
    """In-memory stand-in for the S3 ``Storage`` backend."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.is_configured = True

    def upload(self, fileobj, key, content_type):
        data = fileobj.read()
        self.objects[key] = {"size": len(data), "content_type": content_type}

    def generate_download_url(self, key, expiry_seconds):
        return f"https://s3.example.test/{key}?signed=1"

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def storage() -> Generator[FakeStorage, None, None]:
    fake = FakeStorage()
    app.dependency_overrides[get_storage] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_storage, None)


def _upload(client, headers, *, name="notes.pdf", data=b"hello world", ctype="application/pdf", feature=None):
    form = {"feature": feature} if feature else {}
    return client.post(
        f"{API}/uploads",
        headers=headers,
        files={"file": (name, data, ctype)},
        data=form,
    )


# ---- upload ----

def test_upload(client: TestClient, admin_headers, storage) -> None:
    resp = _upload(client, admin_headers, name="../brief final.pdf", feature="onboarding.documents")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["size_bytes"] == len(b"hello world")
    assert body["feature"] == "onboarding.documents"
    # Filename sanitized + namespaced under the key prefix.
    assert body["storage_key"].startswith("uploads/")
    assert body["storage_key"].endswith("/brief_final.pdf")
    assert body["storage_key"] in storage.objects
    assert body["download_url"].startswith("https://")


def test_upload_rejects_disallowed_type(client: TestClient, admin_headers, storage) -> None:
    resp = _upload(client, admin_headers, name="evil.exe", ctype="application/x-msdownload")
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "unsupported_media_type"


def test_upload_requires_auth(client: TestClient, storage) -> None:
    resp = _upload(client, {})
    assert resp.status_code == 401


# ---- get ----

def test_get_upload(client: TestClient, admin_headers, storage) -> None:
    created = _upload(client, admin_headers)
    upload_id = created.json()["id"]
    got = client.get(f"{API}/uploads/{upload_id}", headers=admin_headers)
    assert got.status_code == 200, got.text
    assert got.json()["id"] == upload_id
    assert got.json()["download_url"].startswith("https://")


def test_get_missing_returns_404(client: TestClient, admin_headers, storage) -> None:
    import uuid

    resp = client.get(f"{API}/uploads/{uuid.uuid4()}", headers=admin_headers)
    assert resp.status_code == 404


# ---- delete ----

def test_delete_upload(client: TestClient, admin_headers, storage) -> None:
    created = _upload(client, admin_headers, name="d.pdf", data=b"data")
    upload_id = created.json()["id"]
    key = created.json()["storage_key"]

    deleted = client.delete(f"{API}/uploads/{upload_id}", headers=admin_headers)
    assert deleted.status_code == 200, deleted.text
    assert key not in storage.objects
    assert client.get(f"{API}/uploads/{upload_id}", headers=admin_headers).status_code == 404


# ---- ownership scoping ----

def test_user_cannot_access_others_upload(client: TestClient, storage, make_user) -> None:
    _, a_headers = make_user(email="a@test.com", password="passwordA1")
    _, b_headers = make_user(email="b@test.com", password="passwordB1")

    created = _upload(client, a_headers, name="a.pdf")
    upload_id = created.json()["id"]

    # B cannot see or delete A's upload — 404 (not 403), no existence leak.
    assert client.get(f"{API}/uploads/{upload_id}", headers=b_headers).status_code == 404
    assert client.delete(f"{API}/uploads/{upload_id}", headers=b_headers).status_code == 404
    # ...but the owner (A) still can.
    assert client.get(f"{API}/uploads/{upload_id}", headers=a_headers).status_code == 200


# ---- graceful degradation when storage is unconfigured ----

def test_upload_503_when_storage_unconfigured(client: TestClient, admin_headers) -> None:
    app.dependency_overrides[get_storage] = lambda: S3Storage(StorageSettings())
    try:
        resp = _upload(client, admin_headers)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "service_unavailable"
    finally:
        app.dependency_overrides.pop(get_storage, None)
