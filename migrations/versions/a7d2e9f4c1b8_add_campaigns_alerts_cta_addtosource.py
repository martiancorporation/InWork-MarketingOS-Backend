"""add campaigns, alerts, post CTA, event campaign link, message add-to-source

Backs several features that had no schema target:
- ``campaigns`` — project-level rollup (targets + actual metrics) for multi-
  campaign management, A/B comparison, and the project-level health score.
- ``alerts`` — KPI watchdog signals (breach of an agreed target) with an
  acknowledge/resolve accountability workflow.
- ``event_posts.cta_label`` / ``cta_url`` — call-to-action on a post.
- ``marketing_events.campaign_id`` — optional grouping of a post/ad under a campaign.
- ``messages.added_to_source_at`` / ``knowledge_source_id`` — manual "add to
  source" promotion of an email into the client's knowledge/RAG layer.

Revision ID: a7d2e9f4c1b8
Revises: f6a3b1c4d5e7
Create Date: 2026-07-16 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d2e9f4c1b8"
down_revision: str | None = "f6a3b1c4d5e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- campaigns ---------------------------------------------------- #
    op.create_table(
        "campaigns",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("objective", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("budget_usd", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("target_cpl", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("target_ctr", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("target_conversion_rate", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("impressions", sa.BigInteger(), nullable=False),
        sa.Column("clicks", sa.BigInteger(), nullable=False),
        sa.Column("conversions", sa.Integer(), nullable=False),
        sa.Column("leads", sa.Integer(), nullable=False),
        sa.Column("spend", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("revenue", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"],
            name=op.f("fk_campaigns_client_id_clients"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name=op.f("fk_campaigns_created_by_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
    )
    op.create_index(op.f("ix_campaigns_client_id"), "campaigns", ["client_id"])
    op.create_index(op.f("ix_campaigns_status"), "campaigns", ["status"])
    op.create_index("ix_campaigns_client_status", "campaigns", ["client_id", "status"])

    # ---- alerts ------------------------------------------------------- #
    op.create_table(
        "alerts",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("metric", sa.String(length=40), nullable=True),
        sa.Column("threshold", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("actual", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("rec_key", sa.String(length=120), nullable=True),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"],
            name=op.f("fk_alerts_client_id_clients"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"],
            name=op.f("fk_alerts_campaign_id_campaigns"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"], ["users.id"],
            name=op.f("fk_alerts_acknowledged_by_users"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"], ["users.id"],
            name=op.f("fk_alerts_resolved_by_users"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
    )
    op.create_index(op.f("ix_alerts_client_id"), "alerts", ["client_id"])
    op.create_index(op.f("ix_alerts_campaign_id"), "alerts", ["campaign_id"])
    op.create_index(op.f("ix_alerts_kind"), "alerts", ["kind"])
    op.create_index(op.f("ix_alerts_severity"), "alerts", ["severity"])
    op.create_index(op.f("ix_alerts_status"), "alerts", ["status"])
    op.create_index("ix_alerts_client_status", "alerts", ["client_id", "status"])
    op.create_index("ix_alerts_client_reckey", "alerts", ["client_id", "rec_key"])

    # ---- marketing_events.campaign_id --------------------------------- #
    op.add_column("marketing_events", sa.Column("campaign_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_marketing_events_campaign_id"), "marketing_events", ["campaign_id"]
    )
    op.create_foreign_key(
        op.f("fk_marketing_events_campaign_id_campaigns"),
        "marketing_events", "campaigns", ["campaign_id"], ["id"], ondelete="SET NULL",
    )

    # ---- event_posts CTA ---------------------------------------------- #
    op.add_column("event_posts", sa.Column("cta_label", sa.String(length=80), nullable=True))
    op.add_column("event_posts", sa.Column("cta_url", sa.Text(), nullable=True))

    # ---- messages add-to-source --------------------------------------- #
    op.add_column(
        "messages", sa.Column("added_to_source_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column("messages", sa.Column("knowledge_source_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "knowledge_source_id")
    op.drop_column("messages", "added_to_source_at")

    op.drop_column("event_posts", "cta_url")
    op.drop_column("event_posts", "cta_label")

    op.drop_constraint(
        op.f("fk_marketing_events_campaign_id_campaigns"), "marketing_events", type_="foreignkey"
    )
    op.drop_index(op.f("ix_marketing_events_campaign_id"), table_name="marketing_events")
    op.drop_column("marketing_events", "campaign_id")

    op.drop_index("ix_alerts_client_reckey", table_name="alerts")
    op.drop_index("ix_alerts_client_status", table_name="alerts")
    op.drop_index(op.f("ix_alerts_status"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_severity"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_kind"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_campaign_id"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_client_id"), table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("ix_campaigns_client_status", table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_status"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_client_id"), table_name="campaigns")
    op.drop_table("campaigns")
