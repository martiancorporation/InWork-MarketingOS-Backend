"""Object-storage settings (Amazon S3). Reads STORAGE_* env vars.

Global file-storage configuration used by the reusable upload system
(``app/services/upload_service.py`` + ``app/integrations/aws/s3.py``). All
optional in local dev — when ``s3_bucket``/``s3_region`` are unset the storage
layer reports ``is_configured is False`` and upload endpoints return 503 rather
than crashing (mirrors how the AI layer degrades without a key).

Credentials are intentionally optional: in production, leave
``aws_access_key_id``/``aws_secret_access_key`` unset and grant the running
task an IAM role — boto3's default credential chain is used automatically.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.env import ENV_FILES

# Sensible default allow-list — documents, images, and common office formats.
# Override per environment via STORAGE_ALLOWED_CONTENT_TYPES (comma-separated).
# The special value "*" disables the allow-list (accept any type).
_DEFAULT_ALLOWED_CONTENT_TYPES = ",".join(
    (
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/svg+xml",
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
    )
)


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        env_prefix="STORAGE_",
        extra="ignore",
        case_sensitive=False,
    )

    provider: str = "s3"  # STORAGE_PROVIDER — reserved for future backends
    s3_bucket: str | None = None  # STORAGE_S3_BUCKET
    s3_region: str | None = None  # STORAGE_S3_REGION, e.g. us-east-1
    # STORAGE_S3_ENDPOINT_URL — set for S3-compatible stores / LocalStack; leave
    # unset for real AWS.
    s3_endpoint_url: str | None = None
    # Credentials — leave unset in prod to use the instance/task IAM role.
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    key_prefix: str = "uploads"  # STORAGE_KEY_PREFIX — base folder inside the bucket
    # Server-side encryption at rest. "AES256" (SSE-S3) or "aws:kms".
    sse: str = "AES256"  # STORAGE_SSE
    presign_expiry_seconds: int = 900  # STORAGE_PRESIGN_EXPIRY_SECONDS (15 min)
    max_upload_bytes: int = 20 * 1024 * 1024  # STORAGE_MAX_UPLOAD_BYTES (20 MB)
    allowed_content_types: str = _DEFAULT_ALLOWED_CONTENT_TYPES

    @property
    def is_configured(self) -> bool:
        return bool(self.s3_bucket and self.s3_region)

    @property
    def allowed_content_types_list(self) -> list[str]:
        return [t.strip() for t in self.allowed_content_types.split(",") if t.strip()]

    def allows_content_type(self, content_type: str) -> bool:
        allowed = self.allowed_content_types_list
        if "*" in allowed:
            return True
        return content_type.strip().lower() in {t.lower() for t in allowed}

    @property
    def max_upload_mb(self) -> int:
        return self.max_upload_bytes // (1024 * 1024)
