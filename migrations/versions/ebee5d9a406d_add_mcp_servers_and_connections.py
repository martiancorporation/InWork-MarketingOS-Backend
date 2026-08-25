"""add mcp servers and connections

Adds the dynamic MCP connector registry (``mcp_servers``) and the per-client
OAuth2 connections against it (``mcp_connections``).

Enum note: ``integration_status`` already exists (created with the
``integrations`` table in adc6dd263daf) and is reused here, so it is declared
with ``create_type=False`` — re-creating it would fail. The two new types
(``mcp_transport``, ``mcp_auth_type``) are created explicitly on upgrade and
dropped on downgrade, since ``DROP TABLE`` does not remove a Postgres enum.

Revision ID: ebee5d9a406d
Revises: 4f1d9a2c2f43
Create Date: 2026-08-14 16:09:54.414932
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ebee5d9a406d"
down_revision: str | None = "4f1d9a2c2f43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Declared with create_type=False so table creation never emits CREATE TYPE;
# the two new types are created/dropped explicitly below.
mcp_transport = postgresql.ENUM("streamable_http", "sse", name="mcp_transport", create_type=False)
mcp_auth_type = postgresql.ENUM("oauth2", "none", name="mcp_auth_type", create_type=False)
integration_status = postgresql.ENUM(
    "connected",
    "disconnected",
    "error",
    "pending",
    name="integration_status",
    create_type=False,
)

_JSON = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), "postgresql"
)


def upgrade() -> None:
    bind = op.get_bind()
    mcp_transport.create(bind, checkfirst=True)
    mcp_auth_type.create(bind, checkfirst=True)

    op.create_table(
        "mcp_servers",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("server_url", sa.String(length=500), nullable=False),
        sa.Column("transport", mcp_transport, nullable=False),
        sa.Column("auth_type", mcp_auth_type, nullable=False),
        sa.Column("oauth_client_id", sa.String(length=255), nullable=True),
        sa.Column("oauth_client_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("oauth_scopes", sa.Text(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("config", _JSON, nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_servers")),
        sa.UniqueConstraint("key", name="uq_mcp_servers_key"),
    )
    op.create_index(op.f("ix_mcp_servers_key"), "mcp_servers", ["key"], unique=False)

    op.create_table(
        "mcp_connections",
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("mcp_server_id", sa.Uuid(), nullable=False),
        sa.Column("status", integration_status, nullable=False),
        sa.Column("account_label", sa.String(length=200), nullable=True),
        sa.Column("external_account_id", sa.String(length=160), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("pkce_verifier_encrypted", sa.Text(), nullable=True),
        sa.Column("client_info_encrypted", sa.Text(), nullable=True),
        sa.Column("config", _JSON, nullable=True),
        sa.Column("last_connected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_mcp_connections_client_id_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mcp_server_id"],
            ["mcp_servers.id"],
            name=op.f("fk_mcp_connections_mcp_server_id_mcp_servers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_connections")),
        sa.UniqueConstraint("client_id", "mcp_server_id", name="uq_mcp_connections_client_server"),
    )
    op.create_index(
        op.f("ix_mcp_connections_client_id"), "mcp_connections", ["client_id"], unique=False
    )
    op.create_index(
        op.f("ix_mcp_connections_mcp_server_id"),
        "mcp_connections",
        ["mcp_server_id"],
        unique=False,
    )
    op.create_index(op.f("ix_mcp_connections_status"), "mcp_connections", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_mcp_connections_status"), table_name="mcp_connections")
    op.drop_index(op.f("ix_mcp_connections_mcp_server_id"), table_name="mcp_connections")
    op.drop_index(op.f("ix_mcp_connections_client_id"), table_name="mcp_connections")
    op.drop_table("mcp_connections")
    op.drop_index(op.f("ix_mcp_servers_key"), table_name="mcp_servers")
    op.drop_table("mcp_servers")

    bind = op.get_bind()
    # Only the types this migration created — `integration_status` is still in
    # use by the `integrations` table and must survive.
    mcp_auth_type.drop(bind, checkfirst=True)
    mcp_transport.drop(bind, checkfirst=True)
