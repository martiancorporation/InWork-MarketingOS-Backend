"""Unit tests: the dashboard cache's "did anything change" fingerprint.

``_hash_inputs`` must be a pure function of ``DashboardSignals`` + the client's
intelligence profile version — the exact two inputs the dashboard AI engines
actually read (see ``DashboardService._signals``/``ContextService.build``).
"""

from __future__ import annotations

from app.ai.dashboard_signals import DashboardSignals, GoalMetric
from app.services.dashboard_service import _hash_inputs


def test_hash_is_stable_for_identical_inputs():
    signals = DashboardSignals(spend=100.0, leads=5, cpl=20.0)
    assert _hash_inputs(signals, 3) == _hash_inputs(signals, 3)


def test_hash_changes_when_a_scalar_signal_changes():
    base = DashboardSignals(spend=100.0, leads=5, cpl=20.0)
    changed = DashboardSignals(spend=150.0, leads=5, cpl=20.0)
    assert _hash_inputs(base, 3) != _hash_inputs(changed, 3)


def test_hash_changes_when_a_list_signal_changes():
    base = DashboardSignals(banned_words=["a"])
    changed = DashboardSignals(banned_words=["a", "b"])
    assert _hash_inputs(base, 1) != _hash_inputs(changed, 1)


def test_hash_changes_when_goal_metrics_change():
    metric = GoalMetric(label="CPL", target=10.0, actual=12.0, higher_is_better=False, on_track=False)
    base = DashboardSignals(goal_metrics=[])
    changed = DashboardSignals(goal_metrics=[metric])
    assert _hash_inputs(base, 1) != _hash_inputs(changed, 1)


def test_hash_changes_when_profile_version_changes():
    signals = DashboardSignals()
    assert _hash_inputs(signals, 1) != _hash_inputs(signals, 2)
    assert _hash_inputs(signals, None) != _hash_inputs(signals, 1)
