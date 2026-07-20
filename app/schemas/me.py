"""Cross-client "what's on you" schemas (BE-04).

Aggregates the current user's outstanding work across every client they can
access: their assigned (non-done) plan tasks, calendar items awaiting approval,
and open KPI alerts — grouped and counted per client for red-dot badges.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class MePendingClient(BaseModel):
    """Per-client roll-up of the current user's outstanding items."""

    client_id: uuid.UUID
    client_name: str
    client_slug: str
    assigned_tasks: int
    pending_approvals: int
    open_alerts: int
    total: int


class MePendingTotals(BaseModel):
    """Grand totals across every accessible client (for the top-level badge)."""

    assigned_tasks: int
    pending_approvals: int
    open_alerts: int
    total: int


class MePendingResponse(BaseModel):
    items: list[MePendingClient]
    total: int  # number of clients with at least one outstanding item
    page: int
    page_size: int
    totals: MePendingTotals
