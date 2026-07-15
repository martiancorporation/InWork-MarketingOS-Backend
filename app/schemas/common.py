"""Shared schema building blocks."""

from __future__ import annotations

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
