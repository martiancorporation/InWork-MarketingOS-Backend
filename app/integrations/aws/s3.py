"""Amazon S3 implementation of the ``Storage`` protocol.

Design notes / best practices baked in:

* **Encryption at rest** on every write (SSE-S3 by default; ``aws:kms`` opt-in).
* **Private objects** — reads go through short-lived presigned GET URLs; nothing
  is ever made public.
* **Signature V4** (required for SSE + newer regions) and bounded retries.
* **Credentials optional** — unset keys fall back to boto3's default provider
  chain (IAM role), the recommended production posture.
* **Lazy SDK import** — ``boto3`` is imported inside ``_client()`` so the app
  (and the test suite, which injects a fake) imports without the dependency.

All boto3 errors are translated into the app's typed ``AppError`` subclasses so
routers keep returning the standard error envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, BinaryIO

from app.core.config.storage import StorageSettings
from app.core.exceptions import NotFoundError, ServiceUnavailableError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mypy_boto3_s3 import S3Client


class S3Storage:
    """Thin, dependency-light wrapper over the S3 client."""

    def __init__(self, settings: StorageSettings) -> None:
        self._settings = settings
        self._client_obj: Any = None

    @property
    def is_configured(self) -> bool:
        return self._settings.is_configured

    # ---- client (lazy) ----

    def _client(self) -> "S3Client":
        if not self._settings.is_configured:
            raise ServiceUnavailableError(
                "File storage is not configured. Set STORAGE_S3_BUCKET and "
                "STORAGE_S3_REGION."
            )
        if self._client_obj is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:  # pragma: no cover - environment guard
                raise ServiceUnavailableError(
                    "The 'boto3' package is required for file storage."
                ) from exc

            session = boto3.session.Session()
            self._client_obj = session.client(
                "s3",
                region_name=self._settings.s3_region,
                endpoint_url=self._settings.s3_endpoint_url or None,
                aws_access_key_id=self._settings.aws_access_key_id or None,
                aws_secret_access_key=self._settings.aws_secret_access_key or None,
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        return self._client_obj

    @property
    def _bucket(self) -> str:
        # is_configured (checked in _client) guarantees this is set.
        return self._settings.s3_bucket  # type: ignore[return-value]

    # ---- Storage protocol ----

    def upload(self, fileobj: BinaryIO, key: str, content_type: str) -> None:
        client = self._client()
        extra = {
            "ContentType": content_type,
            "ServerSideEncryption": self._settings.sse,
        }
        try:
            client.upload_fileobj(fileobj, self._bucket, key, ExtraArgs=extra)
        except Exception as exc:  # noqa: BLE001 - translate any boto error
            raise self._translate(exc, key) from exc

    def generate_download_url(self, key: str, expiry_seconds: int) -> str:
        client = self._client()
        try:
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expiry_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc, key) from exc

    def delete(self, key: str) -> None:
        client = self._client()
        try:
            client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc, key) from exc

    # ---- error translation ----

    @staticmethod
    def _translate(exc: Exception, key: str) -> Exception:
        """Map a botocore error to a typed AppError.

        A 404/NoSuchKey becomes ``NotFoundError``; everything else is a storage
        outage from the caller's perspective (``ServiceUnavailableError``).
        """
        try:
            from botocore.exceptions import ClientError
        except ImportError:  # pragma: no cover
            return ServiceUnavailableError("Storage backend error.")

        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {})
            code = str(error.get("Code", ""))
            status_code = (
                exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            )
            if code in {"404", "NoSuchKey", "NotFound"} or status_code == 404:
                return NotFoundError("Stored file not found.")
        return ServiceUnavailableError("Storage backend is unavailable.")
