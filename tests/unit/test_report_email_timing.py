"""``due_report_dates`` is pure timezone arithmetic — no DB, no fixtures."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.report_email.timing import due_report_dates


def test_before_threshold_only_yesterday_is_due():
    # 2026-08-26 22:00 in New York — before today's 23:30 threshold.
    now_local = datetime(2026, 8, 26, 22, 0, tzinfo=ZoneInfo("America/New_York"))
    now_utc = now_local.astimezone(ZoneInfo("UTC"))
    due = due_report_dates("America/New_York", now_utc)
    assert date(2026, 8, 25) in due
    assert date(2026, 8, 26) not in due


def test_at_and_after_threshold_today_becomes_due():
    # 2026-08-26 23:30:00 in New York local time, expressed in UTC.
    now_local = datetime(2026, 8, 26, 23, 30, tzinfo=ZoneInfo("America/New_York"))
    now_utc = now_local.astimezone(ZoneInfo("UTC"))
    due = due_report_dates("America/New_York", now_utc)
    assert date(2026, 8, 26) in due  # exactly at threshold counts as due
    assert date(2026, 8, 25) in due  # yesterday's threshold always passed


def test_null_timezone_falls_back_to_utc():
    now_utc = datetime(2026, 8, 26, 23, 35, tzinfo=ZoneInfo("UTC"))
    due = due_report_dates(None, now_utc)
    assert date(2026, 8, 26) in due


def test_catch_up_window_is_capped_at_one_day():
    now_utc = datetime(2026, 8, 27, 12, 0, tzinfo=ZoneInfo("UTC"))
    due = due_report_dates(None, now_utc)
    # Two days back must never appear — the outage catch-up is bounded.
    assert date(2026, 8, 25) not in due
    assert date(2026, 8, 26) in due


def test_due_dates_are_a_short_bounded_list():
    now_utc = datetime(2026, 8, 26, 23, 45, tzinfo=ZoneInfo("UTC"))
    due = due_report_dates("Asia/Kolkata", now_utc)
    assert len(due) <= 2
    for d in due:
        assert isinstance(d, date)
