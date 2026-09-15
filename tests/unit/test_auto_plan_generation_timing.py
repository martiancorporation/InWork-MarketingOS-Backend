"""Unit tests: app/services/auto_plan_generation_timing.py (pure, no DB)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.auto_plan_generation_timing import auto_generation_due


def test_not_due_before_the_15th():
    now_utc = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    assert auto_generation_due(None, now_utc) is False


def test_due_on_the_15th():
    now_utc = datetime(2026, 9, 15, 0, 1, tzinfo=UTC)
    assert auto_generation_due(None, now_utc) is True


def test_still_due_after_the_15th_catch_up():
    now_utc = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    assert auto_generation_due(None, now_utc) is True


def test_not_due_at_the_start_of_the_month():
    now_utc = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert auto_generation_due(None, now_utc) is False


def test_respects_client_timezone_boundary():
    # 2026-09-14 23:30 UTC is already 2026-09-15 in a timezone far ahead of UTC.
    now_utc = datetime(2026, 9, 14, 23, 30, tzinfo=UTC)
    assert auto_generation_due("Asia/Kolkata", now_utc) is True
    assert auto_generation_due("UTC", now_utc) is False
