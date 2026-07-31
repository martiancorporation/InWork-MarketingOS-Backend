"""Unit tests: per-feature Anthropic model routing (app/ai/model_router.py)."""

from __future__ import annotations

from app.ai.features import AiFeature
from app.ai.model_router import CHEAP_TIER_FEATURES, MID_TIER_FEATURES, model_for
from app.core.config import get_settings


def test_cheap_tier_features_route_to_cheap_model():
    ai = get_settings().ai
    for feature in CHEAP_TIER_FEATURES:
        assert model_for(feature) == ai.cheap_model


def test_mid_tier_features_route_to_mid_model():
    ai = get_settings().ai
    for feature in MID_TIER_FEATURES:
        assert model_for(feature) == ai.mid_model


def test_ask_ai_features_keep_the_default_model():
    # Deliberately unmapped — client-facing conversational answers stay on
    # whatever AISettings.model is configured (today: the flagship).
    assert model_for(AiFeature.PROJECT_AI) is None
    assert model_for(AiFeature.ASSISTANT) is None


def test_unknown_or_missing_feature_keeps_the_default_model():
    assert model_for("some.unmapped.feature") is None
    assert model_for(None) is None


def test_tiers_do_not_overlap():
    assert not (CHEAP_TIER_FEATURES & MID_TIER_FEATURES)
