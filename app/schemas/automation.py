"""Platform automation schemas — scheduled sweeps and per-client digests.

These back the admin ``/automation`` endpoints and the scheduler process: run the
KPI watchdog across every active client, sync connected integrations, and build
the daily digest (open alerts + integration/onboarding status) for a client.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

# ---- watchdog sweep ---- #


class ClientSweepRow(BaseModel):
    client_id: uuid.UUID
    client_name: str
    opened: int
    updated: int
    auto_resolved: int


class WatchdogSweepResult(BaseModel):
    clients: int
    opened: int
    updated: int
    auto_resolved: int
    per_client: list[ClientSweepRow] = []


# ---- integration sync sweep ---- #


class SyncSweepRow(BaseModel):
    client_id: uuid.UUID
    client_name: str
    key: str
    ok: bool
    error: str | None = None


class SyncSweepResult(BaseModel):
    clients: int
    synced: int
    failed: int
    details: list[SyncSweepRow] = []


# ---- daily digest ---- #


class AlertBrief(BaseModel):
    id: uuid.UUID
    title: str
    severity: str
    metric: str | None = None


class ClientDigest(BaseModel):
    client_id: uuid.UUID
    client_name: str
    status: str
    onboarding_percent: int
    campaign_count: int
    open_alerts: int
    high: int
    medium: int
    low: int
    top_alerts: list[AlertBrief] = []
    connected_integrations: list[str] = []
    pending_integrations: list[str] = []
    generated_at: datetime


class DigestList(BaseModel):
    items: list[ClientDigest]
    total: int


# ---- daily report email sweep ---- #


class DailyReportSweepRow(BaseModel):
    client_id: uuid.UUID
    client_name: str
    report_date: str
    status: str
    error: str | None = None


class DailyReportSweepResult(BaseModel):
    clients: int
    sent: int
    skipped: int
    failed: int
    details: list[DailyReportSweepRow] = []


# ---- automatic month-ahead content plan generation ---- #


class AutoPlanGenerationRow(BaseModel):
    client_id: uuid.UUID
    client_name: str
    period: str  # "YYYY-MM" the plan was generated for
    status: str  # generated | skipped | already_generated | failed
    item_count: int = 0
    error: str | None = None


class AutoPlanGenerationSweepResult(BaseModel):
    clients: int
    generated: int
    skipped: int
    failed: int
    details: list[AutoPlanGenerationRow] = []
