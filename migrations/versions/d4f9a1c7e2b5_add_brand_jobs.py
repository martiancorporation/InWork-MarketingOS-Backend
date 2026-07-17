"""add brand_jobs (async brand-extraction jobs)

Backs the transaction-id + poll flow for brand extraction: a row is created
``pending``, processed in the background, and polled by the client for the
result — so a long scrape/parse doesn't block the request.

Revision ID: d4f9a1c7e2b5
Revises: c8e1b4f7a2d6
Create Date: 2026-07-17 12:30:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4f9a1c7e2b5"
down_revision: str | None = "c8e1b4f7a2d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brand_jobs",
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("document_upload_id", sa.Uuid(), nullable=True),
        sa.Column(
            "result",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"],
            name=op.f("fk_brand_jobs_uploaded_by_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brand_jobs")),
    )
    op.create_index(op.f("ix_brand_jobs_uploaded_by"), "brand_jobs", ["uploaded_by"])
    op.create_index(op.f("ix_brand_jobs_status"), "brand_jobs", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_brand_jobs_status"), table_name="brand_jobs")
    op.drop_index(op.f("ix_brand_jobs_uploaded_by"), table_name="brand_jobs")
    op.drop_table("brand_jobs")
