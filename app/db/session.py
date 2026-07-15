"""Database engine, session factory, and the request-scoped session dependency.

The engine and session factory are created lazily on first use. This keeps the
app importable without a live database driver (useful for tests, tooling, and
OpenAPI generation) and defers the connection pool until it is actually needed.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        db = settings.database
        kwargs: dict = {
            "echo": db.echo,
            "pool_pre_ping": True,  # drop dead connections before handing them out
            "future": True,
        }
        # SQLite (tests/tooling) doesn't use a sized connection pool; only pass
        # pool tuning to real server databases.
        if not db.url.startswith("sqlite"):
            kwargs.update(
                pool_size=db.pool_size,
                max_overflow=db.max_overflow,
                pool_timeout=db.pool_timeout,
                pool_recycle=db.pool_recycle,
            )
        _engine = create_engine(db.url, **kwargs)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autocommit=False, autoflush=False, class_=Session
        )
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a session and always close it."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
