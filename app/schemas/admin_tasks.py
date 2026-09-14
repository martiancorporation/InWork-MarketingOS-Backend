"""Cross-client task view schemas — the Admin Panel's "all tasks" list."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.plan import PlanTaskRead


class AdminTaskRead(PlanTaskRead):
    """A task row enriched with its client's name, for a screen that spans
    every client at once (a bare ``client_id`` isn't useful in that view)."""

    client_name: str


class AdminTaskListResponse(BaseModel):
    items: list[AdminTaskRead]
    total: int
    page: int = 1
    page_size: int = 20
