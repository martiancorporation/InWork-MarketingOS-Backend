"""Small shared helper for "what is today in this client's timezone" — used
anywhere a scheduled/generated action must respect the client's local calendar
day rather than the server's UTC day (report-email timing, AI plan generation,
the monthly auto-generation sweep). Falls back to UTC when a client has no
timezone set, same stance as ``app/services/report_email/timing.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def client_local_now(client_timezone: str | None, *, now_utc: datetime | None = None) -> datetime:
    tz = ZoneInfo(client_timezone) if client_timezone else ZoneInfo("UTC")
    return (now_utc or datetime.now(UTC)).astimezone(tz)


def client_local_today(client_timezone: str | None, *, now_utc: datetime | None = None) -> date:
    return client_local_now(client_timezone, now_utc=now_utc).date()
