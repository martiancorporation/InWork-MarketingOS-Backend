"""add uploads

Global uploaded-file registry backing the reusable S3 upload system: one row
per object in storage, with its key, metadata, an origin ``feature`` tag, and
the uploader (SET NULL on user delete). Not tied to any single feature table.

Revision ID: d4f9a1c72b83
Revises: c3e8f1a04b62
Create Date: 2026-07-13 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4f9a1c72b83"
down_revision: str | None = "c3e8f1a04b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "uploads",
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("feature", sa.String(length=80), nullable=True),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column(
            "meta",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"],
            name=op.f("fk_uploads_uploaded_by_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_uploads")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_uploads_storage_key")),
    )
    op.create_index(op.f("ix_uploads_feature"), "uploads", ["feature"])
    op.create_index(op.f("ix_uploads_uploaded_by"), "uploads", ["uploaded_by"])
    op.create_index("ix_uploads_uploader_created", "uploads", ["uploaded_by", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_uploads_uploader_created", table_name="uploads")
    op.drop_index(op.f("ix_uploads_uploaded_by"), table_name="uploads")
    op.drop_index(op.f("ix_uploads_feature"), table_name="uploads")
    op.drop_table("uploads")
