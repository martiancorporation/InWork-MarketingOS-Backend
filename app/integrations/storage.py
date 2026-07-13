"""Provider-agnostic object-storage contract.

The upload system depends on this ``Storage`` protocol, not on S3 directly, so
the backend can be swapped (S3, GCS, MinIO, a fake in tests) without touching
service or router code. The concrete S3 implementation lives in
``app/integrations/aws/s3.py``.
"""

from __future__ import annotations

from typing import BinaryIO, Protocol, runtime_checkable


@runtime_checkable
class Storage(Protocol):
    """Minimal capability set the upload system needs from a storage backend."""

    @property
    def is_configured(self) -> bool: ...

    def upload(self, fileobj: BinaryIO, key: str, content_type: str) -> None:
        """Upload a file object to ``key`` (encrypted at rest, private)."""
        ...

    def generate_download_url(self, key: str, expiry_seconds: int) -> str:
        """Short-lived presigned GET URL for a private object."""
        ...

    def delete(self, key: str) -> None:
        """Delete the object. Idempotent — deleting a missing key is not an error."""
        ...
