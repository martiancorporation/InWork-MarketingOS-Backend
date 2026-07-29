"""Unit tests for ``resolve_logo_url`` — the read-side fix for the "logo goes
dead after 15 minutes" bug (a raw presigned S3 URL used to be stored directly).
"""

from __future__ import annotations

import uuid

from app.schemas.client import ClientRead, resolve_logo_url
from app.utils.download_link import key_permalink, upload_permalink


def test_resolve_logo_url_none_and_empty() -> None:
    assert resolve_logo_url(None) is None
    assert resolve_logo_url("") is None


def test_resolve_logo_url_external_url_passthrough() -> None:
    assert resolve_logo_url("https://acme.com/logo.svg") == "https://acme.com/logo.svg"
    assert resolve_logo_url("http://acme.com/logo.svg") == "http://acme.com/logo.svg"


def test_resolve_logo_url_upload_id_becomes_permalink() -> None:
    upload_id = uuid.uuid4()
    assert resolve_logo_url(str(upload_id)) == upload_permalink(upload_id)


def test_resolve_logo_url_legacy_bare_key_falls_back_to_key_permalink() -> None:
    key = "uploads/2ead198a-a891-4f57-b037-9d4b5a8ff3ea/logo.png"
    assert resolve_logo_url(key) == key_permalink(key)


def test_client_read_resolves_logo_url_on_validate() -> None:
    upload_id = uuid.uuid4()
    read = ClientRead.model_validate(
        {
            "id": uuid.uuid4(),
            "slug": "acme",
            "name": "Acme",
            "status": "onboarding",
            "pipeline_stage": "onboarding",
            "onboarding_step": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "logo_url": str(upload_id),
        }
    )
    assert read.logo_url == upload_permalink(upload_id)
