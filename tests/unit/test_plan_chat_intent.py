"""Unit tests: app/ai/plan_chat_intent.py's pure parsing helpers + the
unconfigured-provider degrade path (the only branch testable without a real
DB-backed ClientAgent construction — the classify() happy path is covered at
the integration level, tests/integration/test_assistant_plan_chat.py)."""

from __future__ import annotations

from datetime import date

from app.ai.plan_chat_intent import _parse_date_on_or_after, _parse_intent


def test_not_a_plan_request_when_flag_is_false():
    intent = _parse_intent({"wants_content_plan": False}, date(2026, 9, 15))
    assert intent.wants_content_plan is False
    assert intent.ready is False


def test_ready_with_a_valid_future_range():
    intent = _parse_intent(
        {
            "wants_content_plan": True,
            "ready": True,
            "start_date": "2026-09-15",
            "end_date": "2026-09-30",
        },
        date(2026, 9, 15),
    )
    assert intent.wants_content_plan is True
    assert intent.ready is True
    assert intent.start_date == date(2026, 9, 15)
    assert intent.end_date == date(2026, 9, 30)


def test_not_ready_falls_back_to_clarifying_question():
    intent = _parse_intent(
        {"wants_content_plan": True, "ready": False, "clarifying_question": "What dates?"},
        date(2026, 9, 15),
    )
    assert intent.wants_content_plan is True
    assert intent.ready is False
    assert intent.clarifying_question == "What dates?"


def test_missing_clarifying_question_gets_a_default():
    intent = _parse_intent({"wants_content_plan": True, "ready": False}, date(2026, 9, 15))
    assert intent.clarifying_question


def test_a_range_the_model_resolved_before_today_is_not_treated_as_ready():
    """Regression guard: even if 'ready' is claimed true, a start/end date
    before today must never be trusted — this must fall back to asking."""
    intent = _parse_intent(
        {
            "wants_content_plan": True,
            "ready": True,
            "start_date": "2026-09-01",
            "end_date": "2026-09-10",
        },
        date(2026, 9, 15),
    )
    assert intent.ready is False
    assert intent.start_date is None


def test_end_before_start_is_not_ready():
    intent = _parse_intent(
        {
            "wants_content_plan": True,
            "ready": True,
            "start_date": "2026-09-20",
            "end_date": "2026-09-16",
        },
        date(2026, 9, 15),
    )
    assert intent.ready is False


def test_parse_date_on_or_after_rejects_past_dates():
    assert _parse_date_on_or_after("2026-09-01", date(2026, 9, 15)) is None
    assert _parse_date_on_or_after("2026-09-15", date(2026, 9, 15)) == date(2026, 9, 15)
    assert _parse_date_on_or_after("not-a-date", date(2026, 9, 15)) is None
    assert _parse_date_on_or_after(None, date(2026, 9, 15)) is None
