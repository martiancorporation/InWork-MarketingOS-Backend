"""add report_email_log

Delivery record for the daily report email — one row per client per report
date, sent to the internal team assigned to that client (never the client).
The unique constraint on (client_id, report_date) is the idempotency
mechanism the scheduler relies on to avoid double-sending.

Revision ID: d4f7a29c8b1e
Revises: 62e0e3b9228a
Create Date: 2026-08-26 21:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4f7a29c8b1e"
down_revision: str | None = "62e0e3b9228a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_email_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column(
            "recipient_emails",
            sa.JSON(none_as_null=True).with_variant(
                postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), "postgresql"
            ),
            nullable=True,
        ),
        sa.Column("brevo_message_id", sa.String(length=120), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_report_email_log_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_email_log")),
        sa.UniqueConstraint("client_id", "report_date", name=op.f("uq_report_email_log_client_id")),
    )
    op.create_index(
        op.f("ix_report_email_log_client_id"), "report_email_log", ["client_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_report_email_log_client_id"), table_name="report_email_log")
    op.drop_table("report_email_log")
