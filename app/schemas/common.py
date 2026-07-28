"""Shared schema building blocks."""

from __future__ import annotations

from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict

# Upper bound for free-text blobs (message bodies, notes, rules, brand copy).
# Generous for legitimate prose, but stops multi-MB payloads that bloat the DB
# and blow up downstream LLM token cost.
MAX_TEXT = 20_000
# Shorter bound for single-line-ish fields (names, voice, goals summaries).
MAX_LONG_LINE = 2_000


class ORMModel(BaseModel):
    """Base for response schemas populated directly from ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class StrictModel(BaseModel):
    """Base for request bodies that must reject unknown fields.

    Pydantic silently ignores unknown keys by default, so a client typo on a
    PATCH autosave endpoint returns 200 while persisting nothing. ``extra=forbid``
    turns that silent data loss into a clear 422.
    """

    model_config = ConfigDict(extra="forbid")


class MessageResponse(BaseModel):
    detail: str


def validate_timezone(value: str | None) -> str | None:
    """Accept only a real IANA zone name (e.g. ``America/New_York``).

    Shared by onboarding and the admin client edit. Reporting is bucketed by the
    client's *local* day, so a typo here would silently shift every figure we
    show them — validate against the system tz database rather than a
    hand-maintained list, and treat blank as "not set".
    """
    if value is None:
        return None
    tz = value.strip()
    if not tz:
        return None
    if tz not in available_timezones():
        raise ValueError(f"Unknown timezone '{tz}'. Use an IANA name such as 'America/New_York'.")
    return tz
