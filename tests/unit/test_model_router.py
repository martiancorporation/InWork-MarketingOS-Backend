"""Unit tests: dynamic per-feature LLM model routing (app/ai/model_router.py)."""

from __future__ import annotations

import pytest

import app.ai.model_router as router_mod
from app.ai.features import AiFeature
from app.ai.model_router import (
    ALL_CATEGORIES,
    FEATURE_CATEGORY,
    AiTaskCategory,
    bootstrap_model_id,
    category_for,
    invalidate_cache,
    model_for,
)
from app.models.ai_model_route import AiModelRoute

pytestmark = pytest.mark.usefixtures("_reset_router_cache")


@pytest.fixture(autouse=True)
def _reset_router_cache():
    """Every test starts from a clean cache and leaves one behind — this
    module-level cache would otherwise leak state across tests."""
    invalidate_cache()
    yield
    invalidate_cache()


def test_bootstrap_model_id_is_the_cheapest_catalog_model():
    # The fake catalog (tests/conftest.py) has "test-vendor/cheap-fast" as its
    # lowest-input-cost entry — bootstrap_model_id must pick it, not a
    # hardcoded id, and must pick the same one every time (deterministic).
    assert bootstrap_model_id() == "test-vendor/cheap-fast"


def test_unmapped_or_missing_feature_has_no_model():
    assert model_for("some.unmapped.feature") is None
    assert model_for(None) is None
    assert category_for(None) is None
    assert category_for("some.unmapped.feature") is None


def test_mapped_feature_with_no_db_route_has_no_model():
    # No hardcoded fallback anymore — an unconfigured category means no
    # model, treated the same as "AI provider not configured" by the caller.
    assert model_for(AiFeature.HEALTH_SCORE) is None
    assert model_for(AiFeature.PROJECT_AI) is None
    assert model_for(AiFeature.COMMAND_AGENT) is None


def test_qa_review_is_not_routed_here():
    # QA runs on a separate, off-by-default provider (see app/ai/qa.py) and
    # never reaches model_for() — it must not have a category mapping here.
    assert AiFeature.QA_REVIEW not in FEATURE_CATEGORY


def test_every_feature_maps_to_a_real_category():
    for feature, category in FEATURE_CATEGORY.items():
        assert category in ALL_CATEGORIES, f"{feature} maps to an unknown category"


def test_db_route_wins(routed_db):
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

    assert model_for(AiFeature.CONSISTENCY_CHECK) is None


def test_active_route_with_no_model_id_is_ignored(routed_db):
    # Represents a category seeded (self-heal) before the catalog was first
    # reachable — is_active but nothing to actually route to.
    routed_db.add(
        AiModelRoute(
            task_category=AiTaskCategory.CLASSIFICATION,
            model_id=None,
            is_active=True,
        )
    )
    routed_db.commit()
    invalidate_cache()

    assert model_for(AiFeature.CONSISTENCY_CHECK) is None


def test_cache_is_not_reloaded_until_invalidated(routed_db):
    invalidate_cache()
    assert model_for(AiFeature.CONSISTENCY_CHECK) is None

    routed_db.add(
        AiModelRoute(
            task_category=AiTaskCategory.CLASSIFICATION,
            model_id="test/should-not-appear-yet",
            is_active=True,
        )
    )
    routed_db.commit()
    # No invalidate_cache() call — the stale cached value should still win.
    assert model_for(AiFeature.CONSISTENCY_CHECK) is None

    invalidate_cache()
    assert model_for(AiFeature.CONSISTENCY_CHECK) == "test/should-not-appear-yet"


def test_failed_db_load_yields_no_model(monkeypatch):
    def _boom():
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(router_mod, "get_session_factory", _boom)
    invalidate_cache()
    assert model_for(AiFeature.HEALTH_SCORE) is None
