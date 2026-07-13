"""add client intelligence (profiles, directives, knowledge, jobs)

Async post-onboarding intelligence pipeline: durable job queue (intel_jobs),
ingested knowledge sources + pgvector-embedded chunks (knowledge_sources /
knowledge_chunks), versioned client profiles (client_profiles) and their
prioritized directives (client_directives). Adds clients.current_profile_version.

Requires the pgvector extension for the chunk embedding column + ANN index.

Revision ID: e5b2c9f18a47
Revises: d4f9a1c72b83
Create Date: 2026-07-13 13:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e5b2c9f18a47"
down_revision: str | None = "d4f9a1c72b83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1024


def _jsonb():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _embedding():
    # pgvector's vector type on Postgres. Imported here (not at module top) so
    # non-Postgres tooling can still load the migration module.
    from pgvector.sqlalchemy import Vector

    return Vector(EMBEDDING_DIM)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- intel_jobs ----
    op.create_table(
        "intel_jobs",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("job_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", _jsonb(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_by", sa.String(length=80), nullable=True),
        sa.Column("locked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=op.f("fk_intel_jobs_client_id_clients"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_intel_jobs")),
    )
    op.create_index(op.f("ix_intel_jobs_client_id"), "intel_jobs", ["client_id"])
    op.create_index(op.f("ix_intel_jobs_status"), "intel_jobs", ["status"])
    op.create_index("ix_intel_jobs_claim", "intel_jobs", ["status", "run_after"])
    op.create_index("ix_intel_jobs_client_status", "intel_jobs", ["client_id", "status"])

    # ---- knowledge_sources ----
    op.create_table(
        "knowledge_sources",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("ref_kind", sa.String(length=40), nullable=True),
        sa.Column("ref_id", sa.Uuid(), nullable=True),
        sa.Column("ref_key", sa.String(length=120), nullable=True),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=op.f("fk_knowledge_sources_client_id_clients"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_sources")),
    )
    op.create_index(op.f("ix_knowledge_sources_client_id"), "knowledge_sources", ["client_id"])
    op.create_index(op.f("ix_knowledge_sources_ref_key"), "knowledge_sources", ["ref_key"])
    op.create_index(op.f("ix_knowledge_sources_content_hash"), "knowledge_sources", ["content_hash"])
    op.create_index(op.f("ix_knowledge_sources_status"), "knowledge_sources", ["status"])

    # ---- knowledge_chunks ----
    op.create_table(
        "knowledge_chunks",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("embedding", _embedding(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("meta", _jsonb(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=op.f("fk_knowledge_chunks_client_id_clients"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], name=op.f("fk_knowledge_chunks_source_id_knowledge_sources"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
    )
    op.create_index(op.f("ix_knowledge_chunks_client_id"), "knowledge_chunks", ["client_id"])
    op.create_index(op.f("ix_knowledge_chunks_source_id"), "knowledge_chunks", ["source_id"])
    op.create_index(op.f("ix_knowledge_chunks_content_hash"), "knowledge_chunks", ["content_hash"])
    # Approximate-nearest-neighbour index for cosine similarity retrieval.
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_embedding_hnsw ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # ---- client_profiles ----
    op.create_table(
        "client_profiles",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("summary_md", sa.Text(), nullable=True),
        sa.Column("profile", _jsonb(), nullable=True),
        sa.Column("capability_flags", _jsonb(), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("source_hashes", _jsonb(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=op.f("fk_client_profiles_client_id_clients"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_client_profiles_created_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_profiles")),
        sa.UniqueConstraint("client_id", "version", name="uq_client_profile_version"),
    )
    op.create_index(op.f("ix_client_profiles_client_id"), "client_profiles", ["client_id"])
    op.create_index(op.f("ix_client_profiles_status"), "client_profiles", ["status"])

    # ---- client_directives ----
    op.create_table(
        "client_directives",
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("capability_flags", _jsonb(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("conflicts_with_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["client_profiles.id"], name=op.f("fk_client_directives_profile_id_client_profiles"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name=op.f("fk_client_directives_client_id_clients"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], name=op.f("fk_client_directives_source_id_knowledge_sources"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_directives")),
    )
    op.create_index(op.f("ix_client_directives_profile_id"), "client_directives", ["profile_id"])
    op.create_index(op.f("ix_client_directives_client_id"), "client_directives", ["client_id"])
    op.create_index(op.f("ix_client_directives_category"), "client_directives", ["category"])
    op.create_index(op.f("ix_client_directives_status"), "client_directives", ["status"])
    op.create_index("ix_client_directives_client_tier", "client_directives", ["client_id", "tier"])

    # ---- clients.current_profile_version ----
    op.add_column("clients", sa.Column("current_profile_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "current_profile_version")

    op.drop_index("ix_client_directives_client_tier", table_name="client_directives")
    op.drop_index(op.f("ix_client_directives_status"), table_name="client_directives")
    op.drop_index(op.f("ix_client_directives_category"), table_name="client_directives")
    op.drop_index(op.f("ix_client_directives_client_id"), table_name="client_directives")
    op.drop_index(op.f("ix_client_directives_profile_id"), table_name="client_directives")
    op.drop_table("client_directives")

    op.drop_index(op.f("ix_client_profiles_status"), table_name="client_profiles")
    op.drop_index(op.f("ix_client_profiles_client_id"), table_name="client_profiles")
    op.drop_table("client_profiles")

    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding_hnsw")
    op.drop_index(op.f("ix_knowledge_chunks_content_hash"), table_name="knowledge_chunks")
    op.drop_index(op.f("ix_knowledge_chunks_source_id"), table_name="knowledge_chunks")
    op.drop_index(op.f("ix_knowledge_chunks_client_id"), table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.drop_index(op.f("ix_knowledge_sources_status"), table_name="knowledge_sources")
    op.drop_index(op.f("ix_knowledge_sources_content_hash"), table_name="knowledge_sources")
    op.drop_index(op.f("ix_knowledge_sources_ref_key"), table_name="knowledge_sources")
    op.drop_index(op.f("ix_knowledge_sources_client_id"), table_name="knowledge_sources")
    op.drop_table("knowledge_sources")

    op.drop_index("ix_intel_jobs_client_status", table_name="intel_jobs")
    op.drop_index("ix_intel_jobs_claim", table_name="intel_jobs")
    op.drop_index(op.f("ix_intel_jobs_status"), table_name="intel_jobs")
    op.drop_index(op.f("ix_intel_jobs_client_id"), table_name="intel_jobs")
    op.drop_table("intel_jobs")
