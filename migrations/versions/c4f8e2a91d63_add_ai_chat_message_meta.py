"""add ai_chat_messages.meta (chat attachment sidecar)

Ask AI can now take file and image attachments. A message needs somewhere to
record *what* was attached, or the chat renders the files while you send and then
loses them on reload — the reloaded turn is just a content string.

``meta`` is a JSON sidecar rather than a join table because the payload is small,
read only alongside its message, and never queried on its own. ``uploads.meta``
already establishes the pattern. Shape:

    {"attachments": [
        {"upload_id": ..., "filename": ..., "content_type": ...,
         "size_bytes": ..., "storage_key": ...}
    ]}

Note it stores the storage **key**, never a presigned URL: those expire in 15
minutes, and persisting one is exactly what left seven client logos as broken
images. The download URL is signed fresh whenever a chat is read.

JSONB on PostgreSQL, plain JSON elsewhere (the test suite runs on SQLite) — that
variance is handled by ``app/db/types.py:JSONColumn``, so the column type here is
spelled the same way the model spells it.

Revision ID: c4f8e2a91d63
Revises: a7c3f1e05b94
Create Date: 2026-07-28 12:15:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c4f8e2a91d63"
down_revision: str | None = "a7c3f1e05b94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_META = sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


def upgrade() -> None:
    op.add_column("ai_chat_messages", sa.Column("meta", _META, nullable=True))


def downgrade() -> None:
    op.drop_column("ai_chat_messages", "meta")
