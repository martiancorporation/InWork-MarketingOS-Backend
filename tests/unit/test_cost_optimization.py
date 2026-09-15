"""Unit tests: AI cost-optimization advisory report (app/ai/cost_optimization.py).

Tied directly to the live router (`app.ai.model_router.model_for`) rather than
a separate static tier list, so these lock in that the two can never drift.
"""

from __future__ import annotations

from app.ai.cost_optimization import build_report
from app.ai.features import AiFeature
from app.ai.model_router import AiTaskCategory, builtin_default


def _row(**kw) -> dict:
    defaults = {
        "feature": AiFeature.HEALTH_SCORE,
        "model": "anthropic/claude-opus-5",
        "requests": 100,
        "input_tokens": 100_000,
        "output_tokens": 20_000,
        "cache_read_tokens": 0,
        "total_cost": 5.0,
    }
    defaults.update(kw)
    return defaults


def test_suggests_switching_off_the_flagship_for_a_routed_category():
    report = build_report([_row()])
    ids = [s.id for s in report.suggestions]
    assert any(i.startswith(f"route-cheaper-model:{AiFeature.HEALTH_SCORE}:") for i in ids)
    top = report.suggestions[0]
    assert top.suggested_model == builtin_default(AiTaskCategory.ANALYSIS)
    assert top.estimated_savings > 0


def test_no_suggestion_when_already_on_the_recommended_model():
    recommended = builtin_default(AiTaskCategory.ANALYSIS)
    report = build_report([_row(model=recommended)])
    assert report.suggestions == []


def test_no_routing_suggestion_for_an_unmapped_feature():
    report = build_report([_row(feature="some.unmapped.feature")])
    assert not any(s.id.startswith("route-cheaper-model:") for s in report.suggestions)


def test_no_suggestion_for_zero_cost_rows():
    report = build_report([_row(total_cost=0.0)])
    assert report.suggestions == []


def test_prompt_caching_suggestion_for_large_uncached_volume():
    report = build_report(
        [
            _row(
                requests=25,
                input_tokens=250_000,
                output_tokens=5_000,
                cache_read_tokens=0,
                total_cost=1.5,
            )
        ]
    )
    ids = [s.id for s in report.suggestions]
    assert any(i.startswith("enable-caching:") for i in ids)


def test_analyzed_totals_sum_all_rows():
    report = build_report([_row(requests=10, total_cost=1.0), _row(requests=5, total_cost=0.5)])
    assert report.analyzed_requests == 15
    assert abs(report.analyzed_cost - 1.5) < 1e-9
