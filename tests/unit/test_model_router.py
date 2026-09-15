"""Unit tests: dynamic per-feature LLM model routing (app/ai/model_router.py)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.ai.model_router as router_mod
from app.ai.features import AiFeature
from app.ai.model_router import (
    ALL_CATEGORIES,
    FEATURE_CATEGORY,
    KNOWN_MODEL_IDS,
    AiTaskCategory,
    builtin_default,
    category_for,
    invalidate_cache,
    model_for,
)
from app.db.base import Base
from app.models.ai_model_route import AiModelRoute

pytestmark = pytest.mark.usefixtures("_reset_router_cache")


@pytest.fixture(autouse=True)
def _reset_router_cache():
    """Every test starts from a clean cache and leaves one behind — this
    module-level cache would otherwise leak state across tests."""
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def routed_db(monkeypatch):
    """A real (per-test) SQLite engine wired in place of the app's global
    session factory, mirroring how ``app/services/scheduler_service.py``'s
    ``_new_session`` binds to the test engine via ``StaticPool`` — this lets
    ``model_router._load_active_routes`` (which uses its own short-lived
    session, same pattern as ``app/ai/usage.py::record_usage``) actually see
    rows this test commits, instead of hitting a disconnected engine."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    import app.models  # noqa: F401  register every table on Base.metadata

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(router_mod, "get_session_factory", lambda: factory)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_every_mapped_feature_has_a_builtin_default():
    for feature, category in FEATURE_CATEGORY.items():
        assert category in ALL_CATEGORIES
        assert builtin_default(category), f"no default for category of {feature}"


def test_conversational_features_route_off_the_flagship_default():
    # Regression guard: PROJECT_AI (Ask AI) and ASSISTANT (global) used to
    # never consult model_for() at all, so they silently ran on the flagship
    # default model on every single call — the client's core cost complaint.
    conversational_default = builtin_default(AiTaskCategory.CONVERSATIONAL)
    assert model_for(AiFeature.PROJECT_AI) == conversational_default
    assert model_for(AiFeature.ASSISTANT) == conversational_default
    assert conversational_default is not None


def test_category_assignments_match_builtin_defaults_without_db():
    assert model_for(AiFeature.BRAND_EXTRACTION) == builtin_default(AiTaskCategory.EXTRACTION)
    assert model_for(AiFeature.CONSISTENCY_CHECK) == builtin_default(AiTaskCategory.CLASSIFICATION)
    assert model_for(AiFeature.HEALTH_SCORE) == builtin_default(AiTaskCategory.ANALYSIS)
    assert model_for(AiFeature.PLAN_GENERATION) == builtin_default(
        AiTaskCategory.STRUCTURED_GENERATION
    )


def test_unmapped_or_missing_feature_keeps_the_default_model():
    assert model_for("some.unmapped.feature") is None
    assert model_for(None) is None
    assert category_for(None) is None
    assert category_for("some.unmapped.feature") is None


def test_qa_review_is_not_routed_here():
    # QA runs on a separate, off-by-default provider (see app/ai/qa.py) and
    # never reaches model_for() — it must not have a category mapping here.
    assert AiFeature.QA_REVIEW not in FEATURE_CATEGORY


def test_known_models_cover_every_builtin_default():
    for category in ALL_CATEGORIES:
        assert builtin_default(category) in KNOWN_MODEL_IDS


def test_db_override_wins_over_builtin_default(routed_db):
    routed_db.add(
        AiModelRoute(
            task_category=AiTaskCategory.CLASSIFICATION,
            model_id="test/override-model",
            is_active=True,
        )
    )
    routed_db.commit()
    invalidate_cache()

    assert model_for(AiFeature.CONSISTENCY_CHECK) == "test/override-model"


def test_inactive_db_route_is_ignored(routed_db):
    routed_db.add(
        AiModelRoute(
            task_category=AiTaskCategory.CLASSIFICATION,
            model_id="test/inactive-model",
            is_active=False,
        )
    )
    routed_db.commit()
    invalidate_cache()

    assert model_for(AiFeature.CONSISTENCY_CHECK) == builtin_default(AiTaskCategory.CLASSIFICATION)


def test_cache_is_not_reloaded_until_invalidated(routed_db):
    invalidate_cache()
    assert model_for(AiFeature.CONSISTENCY_CHECK) == builtin_default(AiTaskCategory.CLASSIFICATION)

    routed_db.add(
        AiModelRoute(
            task_category=AiTaskCategory.CLASSIFICATION,
            model_id="test/should-not-appear-yet",
            is_active=True,
        )
    )
    routed_db.commit()
    # No invalidate_cache() call — the stale cached value should still win.
    assert model_for(AiFeature.CONSISTENCY_CHECK) == builtin_default(AiTaskCategory.CLASSIFICATION)

    invalidate_cache()
    assert model_for(AiFeature.CONSISTENCY_CHECK) == "test/should-not-appear-yet"


def test_failed_db_load_falls_back_to_builtin_default(monkeypatch):
    def _boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(router_mod, "get_session_factory", _boom)
    invalidate_cache()
    assert model_for(AiFeature.HEALTH_SCORE) == builtin_default(AiTaskCategory.ANALYSIS)
