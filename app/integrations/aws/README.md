# `app/integrations/aws`

Amazon Web Services client glue.

## `s3.py` — `S3Storage`

Concrete implementation of the provider-agnostic `Storage` protocol
(`app/integrations/storage.py`), used by the global upload system
(`app/services/upload_service.py`). Three capabilities: `upload`,
`generate_download_url`, `delete`.

Best practices applied:

- **Encryption at rest** on every write (SSE-S3 by default; `aws:kms` opt-in via
  `STORAGE_SSE`).
- **Private objects only** — downloads use short-lived presigned GET URLs.
- **Signature V4** + bounded retries.
- **Credentials optional** — unset keys fall back to boto3's default provider
  chain (IAM role), the recommended production posture.
- **Lazy `boto3` import** — importing this module never requires the SDK, so the
  app imports (and tests, which inject a fake `Storage`) without it.

All botocore errors are translated to the app's typed `AppError` subclasses so
routers keep returning the standard error envelope.

Configuration lives in `app/core/config/storage.py` (`STORAGE_*` env vars).
