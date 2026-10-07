"""Unit tests: Google Ads ``leads`` reconciliation in ``_normalize``.

Real-world trigger (Sept 2026, Tony's Garage): a client saw "12 calls, 24
calls" in Google Ads' own Calls report while our reported leads were near
zero. Root cause — ``metrics.conversions`` only counts a call once it clears a
minimum duration threshold, while the raw call volume the client actually
sees lives in a *different* metric (``metrics.phone_calls``, queryable only
from ``campaign``). ``_normalize`` reconciles the two: replace the
call-category slice of ``conversions`` with the full raw call count, so real
calls are neither dropped nor double-counted.
"""

from __future__ import annotations

from datetime import date

from app.integrations.google.ads import _normalize


def _base_row(day: str, *, conversions: float) -> dict:
    return {
        "segments": {"date": day},
        "metrics": {
            "impressions": 100,
            "clicks": 10,
            "costMicros": 5_000_000,
            "conversions": conversions,
            "conversionsValue": 20,
        },
    }


def test_raw_phone_calls_replace_the_call_category_slice_of_conversions() -> None:
    """All 4 conversions on this day are call-based; the real call volume is
    18 — leads should end up at 18, not 4 and not 22 (double-counted)."""
    base_payload = {"data": [{"results": [_base_row("2026-09-11", conversions=4)]}]}
    call_conversion_rows = [
        {
            "segments": {"date": "2026-09-11", "conversionActionCategory": "PHONE_CALL_LEAD"},
            "metrics": {"conversions": 4},
        }
    ]
    phone_call_rows = [{"segments": {"date": "2026-09-11"}, "metrics": {"phoneCalls": 18}}]

    rows = _normalize(base_payload, call_conversion_rows, phone_call_rows)

    assert len(rows) == 1
    assert rows[0]["conversions"] == 4  # unchanged — still matches Google's own Conversions column
    assert rows[0]["leads"] == 18


def test_non_call_conversions_are_preserved_alongside_calls() -> None:
    """2 of 5 conversions are call-based; the other 3 (e.g. a Contact Us form)
    have nothing to do with calls and must survive the reconciliation."""
    base_payload = {"data": [{"results": [_base_row("2026-09-12", conversions=5)]}]}
    call_conversion_rows = [
        {
            "segments": {"date": "2026-09-12", "conversionActionCategory": "PHONE_CALL_LEAD"},
            "metrics": {"conversions": 2},
        },
        {
            "segments": {"date": "2026-09-12", "conversionActionCategory": "SUBMIT_LEAD_FORM"},
            "metrics": {"conversions": 3},
        },
    ]
    phone_call_rows = [{"segments": {"date": "2026-09-12"}, "metrics": {"phoneCalls": 7}}]

    rows = _normalize(base_payload, call_conversion_rows, phone_call_rows)

    assert rows[0]["conversions"] == 5
    assert rows[0]["leads"] == 3 + 7  # non-call conversions + full raw call volume


def test_no_calls_at_all_falls_back_to_plain_conversions() -> None:
    base_payload = {"data": [{"results": [_base_row("2026-09-13", conversions=6)]}]}

    rows = _normalize(base_payload, call_conversion_rows=[], phone_call_rows=[])

    assert rows[0]["conversions"] == 6
    assert rows[0]["leads"] == 6


def test_leads_never_goes_negative_on_a_data_mismatch() -> None:
    """Defensive: if the call-category slice ever reports more than total
    conversions (a transient Google Ads reporting inconsistency), the
    non-call remainder is floored at zero rather than going negative."""
    base_payload = {"data": [{"results": [_base_row("2026-09-14", conversions=2)]}]}
    call_conversion_rows = [
        {
            "segments": {"date": "2026-09-14", "conversionActionCategory": "PHONE_CALL_LEAD"},
            "metrics": {"conversions": 5},
        }
    ]
    phone_call_rows = [{"segments": {"date": "2026-09-14"}, "metrics": {"phoneCalls": 9}}]

    rows = _normalize(base_payload, call_conversion_rows, phone_call_rows)

    assert rows[0]["leads"] == 9  # max(2 - 5, 0) + 9


def test_dates_with_no_matching_call_rows_are_unaffected() -> None:
    base_payload = {
        "data": [
            {
                "results": [
                    _base_row("2026-09-10", conversions=1),
                    _base_row("2026-09-11", conversions=0),
                ]
            }
        ]
    }
    call_conversion_rows = [
        {
            "segments": {"date": "2026-09-10", "conversionActionCategory": "PHONE_CALL_LEAD"},
            "metrics": {"conversions": 1},
        }
    ]
    phone_call_rows = [{"segments": {"date": "2026-09-10"}, "metrics": {"phoneCalls": 3}}]

    rows = _normalize(base_payload, call_conversion_rows, phone_call_rows)
    by_date = {r["date"]: r for r in rows}

    assert by_date[date(2026, 9, 10)]["leads"] == 3  # (1 - 1) + 3
    assert by_date[date(2026, 9, 11)]["leads"] == 0  # no conversions, no calls that day
