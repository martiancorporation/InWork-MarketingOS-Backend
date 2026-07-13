"""Unit tests for upload validation, filename sanitization, and settings."""

from __future__ import annotations

import pytest

from app.core.config.storage import StorageSettings
from app.core.exceptions import (
    BadRequestError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.services.upload_service import UploadService, sanitize_filename


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\brief.docx", "brief.docx"),
        ("my file (final).png", "my_file__final_.png"),
        ("...hidden", "hidden"),
        ("", "file"),
        ("/", "file"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_to_255() -> None:
    assert len(sanitize_filename("a" * 500 + ".pdf")) == 255


def test_allowed_content_types() -> None:
    s = StorageSettings()
    assert s.allows_content_type("application/pdf")
    assert s.allows_content_type("IMAGE/PNG")  # case-insensitive
    assert not s.allows_content_type("application/x-msdownload")


def test_wildcard_allows_anything() -> None:
    s = StorageSettings(allowed_content_types="*")
    assert s.allows_content_type("application/anything")


def _service(settings: StorageSettings) -> UploadService:
    # db/storage are unused by _validate, so None/None is fine here.
    return UploadService(db=None, storage=None, settings=settings)  # type: ignore[arg-type]


def test_validate_rejects_oversize() -> None:
    svc = _service(StorageSettings(max_upload_bytes=10))
    with pytest.raises(PayloadTooLargeError):
        svc._validate("a.pdf", "application/pdf", 11)


def test_validate_rejects_bad_type() -> None:
    svc = _service(StorageSettings())
    with pytest.raises(UnsupportedMediaTypeError):
        svc._validate("a.exe", "application/x-msdownload", 5)


def test_validate_rejects_empty() -> None:
    svc = _service(StorageSettings())
    with pytest.raises(BadRequestError):
        svc._validate("  ", "application/pdf", 5)
    with pytest.raises(BadRequestError):
        svc._validate("a.pdf", "application/pdf", 0)


def test_validate_accepts_good_input() -> None:
    svc = _service(StorageSettings(max_upload_bytes=1000))
    svc._validate("a.pdf", "application/pdf", 500)  # no raise
