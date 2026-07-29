"""Integration tests for the file API (upload / get / delete).

The S3 backend is replaced with an in-memory ``FakeStorage`` via a dependency
override, so nothing touches AWS. One test uses the real (unconfigured)
``S3Storage`` to assert the graceful 503 degradation.
"""

from __future__ import annotations

from collections.abc import Generator
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_storage
from app.core.config.storage import StorageSettings
from app.integrations.aws import S3Storage
from app.main import app
from app.utils.download_link import key_permalink, upload_permalink

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


def _path_and_query(url: str) -> str:
    """A permalink is absolute (``PUBLIC_API_BASE_URL``); TestClient only talks to
    the app under test, so strip it down to what `client.get` needs."""
    parsed = urlparse(url)
    return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path


def _upload(
    client, headers, *, name="notes.pdf", data=b"hello world", ctype="application/pdf", feature=None
):
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
    # The permanent permalink, not a raw presigned URL — never expires itself.
    assert body["download_url"] == upload_permalink(body["id"])


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
    assert got.json()["download_url"] == upload_permalink(upload_id)


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


# ---- permanent, signed download redirect ----


def test_download_upload_redirects(client: TestClient, admin_headers, storage) -> None:
    created = _upload(client, admin_headers, name="logo.png", ctype="image/png")
    body = created.json()

    resp = client.get(_path_and_query(body["download_url"]), follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == f"https://s3.example.test/{body['storage_key']}?signed=1"


def test_download_upload_needs_no_bearer_auth(client: TestClient, admin_headers, storage) -> None:
    """Deliberate: browsers can't attach Authorization to <img src>/<a href>, so
    possession of a validly-signed link is the whole authorization here."""
    created = _upload(client, admin_headers, name="logo.png", ctype="image/png")
    body = created.json()

    resp = client.get(_path_and_query(body["download_url"]), follow_redirects=False)

    assert resp.status_code == 302


def test_download_upload_tampered_signature_is_404(
    client: TestClient, admin_headers, storage
) -> None:
    created = _upload(client, admin_headers)
    upload_id = created.json()["id"]

    resp = client.get(f"{API}/uploads/{upload_id}/download?sig=" + "0" * 64, follow_redirects=False)

    assert resp.status_code == 404


def test_download_upload_after_delete_is_404(client: TestClient, admin_headers, storage) -> None:
    created = _upload(client, admin_headers)
    body = created.json()
    client.delete(f"{API}/uploads/{body['id']}", headers=admin_headers)

    resp = client.get(_path_and_query(body["download_url"]), follow_redirects=False)

    assert resp.status_code == 404


def test_download_survives_past_the_old_presign_ttl_conceptually(
    client: TestClient, admin_headers, storage
) -> None:
    """The permalink itself carries no expiry — repeated hits keep 302ing, unlike
    the old direct presigned URL which died after 900s."""
    created = _upload(client, admin_headers, name="logo.png", ctype="image/png")
    body = created.json()
    path = _path_and_query(body["download_url"])

    first = client.get(path, follow_redirects=False)
    second = client.get(path, follow_redirects=False)

    assert first.status_code == second.status_code == 302


def test_download_by_key_redirects(client: TestClient, storage) -> None:
    key = "uploads/legacy-no-upload-row/logo.png"
    storage.objects[key] = {"size": 1, "content_type": "image/png"}

    resp = client.get(_path_and_query(key_permalink(key)), follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == f"https://s3.example.test/{key}?signed=1"


def test_download_by_key_tampered_signature_is_404(client: TestClient, storage) -> None:
    resp = client.get(f"{API}/uploads/by-key/download?key=whatever&sig=" + "0" * 64)
    assert resp.status_code == 404
