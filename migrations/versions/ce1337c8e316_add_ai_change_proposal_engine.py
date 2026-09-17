"""add ai change-proposal engine

Adds the governed propose -> approve -> execute pipeline behind the AI chat's
natural-language command layer: ``change_proposals`` (one AI-drafted batch of
mutations) and ``proposed_operations`` (each individual staged mutation,
dry-run validated but not yet applied). Also adds a nullable, indexed
``proposal_id`` column to the existing ``audit_log`` table so "which AI
proposal produced this row" is a fast, first-class query instead of a scan
over ``meta``.

Revision ID: ce1337c8e316
Revises: 53d83c86a848
Create Date: 2026-09-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ce1337c8e316"
down_revision: str | None = "53d83c86a848"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

proposal_status = postgresql.ENUM(
    "draft",
    "pending_approval",
    "executing",
    "completed",
    "failed",
    "cancelled",
    "expired",
    name="proposal_status",
    create_type=False,
)
proposed_operation_status = postgresql.ENUM(
    "pending", "executed", "failed", "skipped", name="proposed_operation_status", create_type=False
)
proposed_operation_type = postgresql.ENUM(
    "create",
    "update",
    "delete",
    "assign",
    "unassign",
    name="proposed_operation_type",
    create_type=False,
)

_JSON = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), "postgresql"
)


def upgrade() -> None:
    bind = op.get_bind()
    proposal_status.create(bind, checkfirst=True)
    proposed_operation_status.create(bind, checkfirst=True)
    proposed_operation_type.create(bind, checkfirst=True)

    op.create_table(
        "change_proposals",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("status", proposal_status, nullable=False),
        sa.Column("raw_request", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("executed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"],
            name=op.f("fk_change_proposals_approved_by_user_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["ai_chats.id"],
            name=op.f("fk_change_proposals_chat_id_ai_chats"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"],
            name=op.f("fk_change_proposals_client_id_clients"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name=op.f("fk_change_proposals_created_by_user_id_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["ai_chat_messages.id"],
            name=op.f("fk_change_proposals_message_id_ai_chat_messages"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_proposals")),
    )
    op.create_index(
        op.f("ix_change_proposals_client_id"), "change_proposals", ["client_id"],
    )
    op.create_index(
        op.f("ix_change_proposals_chat_id"), "change_proposals", ["chat_id"],
    )
    op.create_index(
        op.f("ix_change_proposals_status"), "change_proposals", ["status"],
    )
    op.create_index(
        op.f("ix_change_proposals_expires_at"), "change_proposals", ["expires_at"],
    )
    op.create_index(
        "ix_change_proposals_client_status", "change_proposals", ["client_id", "status"],
    )

    op.create_table(
        "proposed_operations",
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("operation_type", proposed_operation_type, nullable=False),
        sa.Column("entity_type", sa.String(length=60), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("entity_label", sa.String(length=200), nullable=True),
        sa.Column("field_changes", _JSON, nullable=True),
        sa.Column("payload", _JSON, nullable=True),
        sa.Column("base_snapshot", _JSON, nullable=True),
        sa.Column("required_capability", sa.String(length=60), nullable=True),
        sa.Column("requires_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", proposed_operation_status, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["change_proposals.id"],
            name=op.f("fk_proposed_operations_proposal_id_change_proposals"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposed_operations")),
        sa.UniqueConstraint(
            "proposal_id", "seq", name=op.f("uq_proposed_operations_proposal_id")
        ),
    )
    # ORM-only default going forward; the server_default above just satisfies
    # existing-row backfill for a NOT NULL column added to an empty table.
    op.alter_column("proposed_operations", "requires_admin", server_default=None)
    op.create_index(
        op.f("ix_proposed_operations_proposal_id"), "proposed_operations", ["proposal_id"],
    )

    op.add_column("audit_log", sa.Column("proposal_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_audit_log_proposal_id_change_proposals"),
        "audit_log", "change_proposals", ["proposal_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(op.f("ix_audit_log_proposal_id"), "audit_log", ["proposal_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_log_proposal_id"), table_name="audit_log")
    op.drop_constraint(
        op.f("fk_audit_log_proposal_id_change_proposals"), "audit_log", type_="foreignkey"
    )
    op.drop_column("audit_log", "proposal_id")

    op.drop_index(op.f("ix_proposed_operations_proposal_id"), table_name="proposed_operations")
    op.drop_table("proposed_operations")

    op.drop_index("ix_change_proposals_client_status", table_name="change_proposals")
    op.drop_index(op.f("ix_change_proposals_expires_at"), table_name="change_proposals")
    op.drop_index(op.f("ix_change_proposals_status"), table_name="change_proposals")
    op.drop_index(op.f("ix_change_proposals_chat_id"), table_name="change_proposals")
    op.drop_index(op.f("ix_change_proposals_client_id"), table_name="change_proposals")
    op.drop_table("change_proposals")

    bind = op.get_bind()
    proposed_operation_type.drop(bind, checkfirst=True)
    proposed_operation_status.drop(bind, checkfirst=True)
    proposal_status.drop(bind, checkfirst=True)
