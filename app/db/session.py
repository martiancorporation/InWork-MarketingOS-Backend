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
        _engine = create_engine(
            settings.database.url,
            echo=settings.database.echo,
            pool_pre_ping=True,
            future=True,
        )
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
