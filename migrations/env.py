"""Alembic migration environment.

Pulls the database URL from the app settings and targets ``Base.metadata`` so
``alembic revision --autogenerate`` sees every model.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  — imported for side effect: register all tables
from app.core.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the URL from app config (keeps secrets out of alembic.ini).
config.set_main_option("sqlalchemy.url", get_settings().database.url)

target_metadata = Base.metadata

# Indexes that exist in Postgres but deliberately cannot be declared on the
# models, so autogenerate must not propose dropping them.
#
# `ix_knowledge_chunks_embedding_hnsw` is the pgvector HNSW index backing RAG
# similarity search. It's created with raw DDL in `e5b2c9f18a47` because
# `Base.metadata` is shared with the SQLite test database, which has no `hnsw`
# access method — declaring it on the model would break `create_all()` in the
# whole test suite. Dropping it in production would silently turn every
# similarity search into a sequential scan.
#
# `ix_uploads_uploader_created` predates this hook and is not referenced by any
# query today (`uploads.uploaded_by` already has its own single-column index).
# It's preserved rather than dropped because removing a production index is a
# deliberate decision, not a side effect of an autogenerate diff.
_DB_MANAGED_INDEXES = {
    "ix_knowledge_chunks_embedding_hnsw",
    "ix_uploads_uploader_created",
}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Keep `alembic check` honest about genuine model/migration drift by
    filtering out the indexes above, which live only in the database."""
    if type_ == "index" and name in _DB_MANAGED_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations without a DB connection (emit SQL)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
