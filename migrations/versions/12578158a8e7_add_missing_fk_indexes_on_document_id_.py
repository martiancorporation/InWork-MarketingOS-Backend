"""add missing fk indexes on document_id/upload_id columns

Four foreign-key columns had no index at all: ``compliance_docs.document_id``,
``message_attachments.document_id``, ``event_assets.document_id``, and
``support_ticket_attachments.upload_id`` — all reference ``documents``/``uploads``
with ``ondelete="CASCADE"``, so deleting a document/upload row requires a
sequential scan of the child table to find cascade targets without these.

Autogenerate also detected two unrelated, pre-existing drift items (a
``knowledge_chunks`` HNSW vector index and ``ix_uploads_uploader_created``,
both created outside any tracked migration — see
``4328bd02dae3_add_dashboard_snapshots.py``'s docstring) as "removed" — those
are intentionally NOT touched here; this migration only adds the four new
indexes.

Revision ID: 12578158a8e7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-02 22:23:54.069491
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "12578158a8e7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_compliance_docs_document_id"), "compliance_docs", ["document_id"], unique=False
    )
    op.create_index(
        op.f("ix_event_assets_document_id"), "event_assets", ["document_id"], unique=False
    )
    op.create_index(
        op.f("ix_message_attachments_document_id"),
        "message_attachments",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_support_ticket_attachments_upload_id"),
        "support_ticket_attachments",
        ["upload_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_support_ticket_attachments_upload_id"), table_name="support_ticket_attachments"
    )
    op.drop_index(op.f("ix_message_attachments_document_id"), table_name="message_attachments")
    op.drop_index(op.f("ix_event_assets_document_id"), table_name="event_assets")
    op.drop_index(op.f("ix_compliance_docs_document_id"), table_name="compliance_docs")
