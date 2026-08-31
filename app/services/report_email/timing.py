"""When a client's daily report becomes due — pure, timezone-only logic.

Deliberately has no DB/network dependency so it's cheap to unit test every
edge case (missing timezone, exactly-at-threshold, catch-up window) without a
session or fixtures.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

REPORT_HOUR = 23
REPORT_MINUTE = 30
_CATCH_UP_DAYS = 1  # how far back a missed tick self-heals; see module docstring context


def due_report_dates(client_timezone: str | None, now_utc: datetime) -> list[date]:
    """Client-local dates whose 23:30 send threshold has passed.

    Always includes yesterday's local date (its threshold, by definition, has
    always already passed) as a bounded, one-day self-heal for a missed tick —
    the caller checks ``report_email_log`` per candidate and skips whatever's
    already sent, so this being "always a candidate" costs one cheap lookup,
    not a duplicate send. Today's local date is included only once its own
    23:30 threshold has passed. A multi-day outage will NOT backfill further
    than one day — that's a deliberate cap, not an oversight.
    """
    tz = ZoneInfo(client_timezone) if client_timezone else ZoneInfo("UTC")
    now_local = now_utc.astimezone(tz)

    due: list[date] = []
    for d in (now_local.date() - timedelta(days=_CATCH_UP_DAYS), now_local.date()):
        threshold = datetime.combine(d, time(REPORT_HOUR, REPORT_MINUTE), tzinfo=tz)
        if now_local >= threshold:
            due.append(d)
    return due
