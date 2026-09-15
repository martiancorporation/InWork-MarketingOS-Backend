"""When a client's automatic month-ahead content plan becomes due — pure,
timezone-only logic, mirroring ``app/services/report_email/timing.py``.

Deliberately has no DB dependency so it's cheap to unit test. The real
idempotency guard is ``AutoPlanGenerationLog`` (checked by the caller); this
function only answers "is it *at least* the 15th, client-local" — the day
threshold that gives management the rest of the month to review before the
next one starts.
"""

from __future__ import annotations

from datetime import datetime

from app.utils.timezones import client_local_today

_DUE_DAY_OF_MONTH = 15


def auto_generation_due(client_timezone: str | None, now_utc: datetime) -> bool:
    """True once the client-local calendar day is on or after the 15th.

    "On or after" (not "exactly") is what makes this catch-up safe: a missed
    tick on the 15th (a restart, a deploy) still fires on the 16th, 17th, etc.
    Real de-duplication — never generating the same month twice — is the
    caller's job, via a check against ``AutoPlanGenerationLog``.
    """
    today = client_local_today(client_timezone, now_utc=now_utc)
    return today.day >= _DUE_DAY_OF_MONTH
