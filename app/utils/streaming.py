"""Bounded, streaming reads of an ``UploadFile``.

Reading ``await file.read()`` in one shot buffers the whole body before any
size check runs — a hostile (or just large) upload can exhaust memory before
the app gets a chance to reject it. ``read_capped`` reads in fixed chunks and
aborts the moment the cap is exceeded, so the most it ever buffers is
``max_bytes + one chunk``.
"""

from __future__ import annotations

from fastapi import UploadFile

from app.core.exceptions import PayloadTooLargeError

_READ_CHUNK = 1024 * 1024  # 1 MB


async def read_capped(file: UploadFile, max_bytes: int, *, chunk_size: int = _READ_CHUNK) -> bytes:
    """Read ``file`` into memory, bounded by ``max_bytes``.

    Raises ``PayloadTooLargeError`` the moment the running total exceeds the
    cap, without ever buffering the full oversized body.
    """
    buffer = bytearray()
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise PayloadTooLargeError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    return bytes(buffer)
