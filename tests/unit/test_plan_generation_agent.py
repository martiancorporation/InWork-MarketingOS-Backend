"""Unit tests: the pure date-clamping/fallback helpers in app/ai/plan_generation.py.

Focused on the past-date bug fix — these clamp/fallback functions are the
actual mechanism that guarantees no generated item lands before the requested
``start_date``, regardless of what the model returns.
"""

from __future__ import annotations

from datetime import date

from app.ai.plan_generation import (
    _fallback_range,
    _format_range_label,
    _parse_and_clamp_date,
)


def test_clamp_accepts_a_date_within_range():
    assert _parse_and_clamp_date("2026-09-20", date(2026, 9, 15), date(2026, 9, 30)) == date(
        2026, 9, 20
    )


def test_clamp_rejects_a_date_before_start():
    result = _parse_and_clamp_date("2026-09-01", date(2026, 9, 15), date(2026, 9, 30))
    assert result >= date(2026, 9, 15)


def test_clamp_rejects_a_date_after_end():
    result = _parse_and_clamp_date("2026-10-05", date(2026, 9, 15), date(2026, 9, 30))
    assert result <= date(2026, 9, 30)


def test_clamp_handles_unparseable_input():
    result = _parse_and_clamp_date("not-a-date", date(2026, 9, 15), date(2026, 9, 30))
    assert date(2026, 9, 15) <= result <= date(2026, 9, 30)


def test_clamp_handles_none_input():
    result = _parse_and_clamp_date(None, date(2026, 9, 15), date(2026, 9, 30))
    assert date(2026, 9, 15) <= result <= date(2026, 9, 30)


def test_fallback_range_never_lands_before_start_date():
    items = _fallback_range(date(2026, 9, 15), date(2026, 9, 30))
    assert items
    for item in items:
        assert date(2026, 9, 15) <= item.event_date <= date(2026, 9, 30)


def test_fallback_range_handles_a_single_day_range():
    items = _fallback_range(date(2026, 9, 15), date(2026, 9, 15))
    assert items
    assert all(item.event_date == date(2026, 9, 15) for item in items)


def test_format_range_label_same_month():
    assert _format_range_label(date(2026, 9, 15), date(2026, 9, 30)) == "September 15–30, 2026"


def test_format_range_label_spans_months():
    label = _format_range_label(date(2026, 9, 25), date(2026, 10, 5))
    assert "September 25, 2026" in label
    assert "October 5, 2026" in label
