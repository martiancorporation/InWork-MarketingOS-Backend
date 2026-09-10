"""Platform automation API (v1) — admin-only cross-client automation triggers.

- ``POST /automation/watchdog/run``        — run the KPI watchdog across all active clients
- ``POST /automation/integrations/sync``   — sync every connected integration
- ``GET  /automation/digest``              — daily digest for all active clients
- ``GET  /automation/clients/{id}/digest`` — daily digest for one client
- ``POST /automation/report-email/run``    — run the daily report email sweep now
- ``POST /automation/clients/{id}/report-email/send`` — send one client's report now (QA)

These are platform-wide operations, so they require an administrator. The same
service methods are driven on a cadence by the scheduler process
(``python -m app.scheduler``).

Each sweep processes up to ``SCHEDULER_SWEEP_CONCURRENCY`` clients at once
(``SchedulerService``), which keeps these requests well under gunicorn's
graceful timeout at realistic client counts. If the active-client count grows
enough that even bounded-concurrency sweeps risk that timeout, convert these
routes to return ``202 Accepted`` and run the sweep via ``BackgroundTasks``
(mirroring the existing async ``BrandJob`` pattern) rather than raising the
concurrency further.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, DbSession
from app.core.exceptions import NotFoundError
from app.core.rate_limit import RateLimit
from app.models.client import Client
from app.schemas.automation import (
    ClientDigest,
    DailyReportSweepResult,
    DigestList,
    SyncSweepResult,
    WatchdogSweepResult,
)
from app.services.report_email.service import ReportEmailService
from app.services.scheduler_service import SchedulerService

router = APIRouter(prefix="/automation", tags=["automation"])

# These sweeps are the most expensive routes in the app — the report-email
# one makes an AI provider call *and* sends real email per client. Admin-only
# is an authorization control, not a cost control: a retry loop or a
# double-click shouldn't be able to bill twice or double-send to clients.
_SWEEP_RATE_LIMIT = RateLimit("automation_sweep", times=2, seconds=300)


@router.post(
    "/watchdog/run",
    dependencies=[Depends(_SWEEP_RATE_LIMIT)],
    response_model=WatchdogSweepResult,
    summary="Run the KPI watchdog across all active clients (admin)",
)
async def run_watchdog(admin: AdminUser, db: DbSession) -> WatchdogSweepResult:
    return await SchedulerService(db).run_watchdog_sweep()


@router.post(
    "/integrations/sync",
    dependencies=[Depends(_SWEEP_RATE_LIMIT)],
    response_model=SyncSweepResult,
    summary="Sync every connected integration across active clients (admin)",
)
async def sync_integrations(admin: AdminUser, db: DbSession) -> SyncSweepResult:
    return await SchedulerService(db).sync_integrations_sweep()


@router.get(
    "/digest", response_model=DigestList, summary="Daily digest for all active clients (admin)"
)
def all_digests(admin: AdminUser, db: DbSession) -> DigestList:
    return SchedulerService(db).build_all_digests()


@router.get(
    "/clients/{client_id}/digest",
    response_model=ClientDigest,
    summary="Daily digest for one client (admin)",
)
def client_digest(client_id: uuid.UUID, admin: AdminUser, db: DbSession) -> ClientDigest:
    return SchedulerService(db).build_digest(client_id)


@router.post(
    "/report-email/run",
    dependencies=[Depends(_SWEEP_RATE_LIMIT)],
    response_model=DailyReportSweepResult,
    summary="Run the daily report email sweep across all active clients now (admin)",
)
async def run_report_email_sweep(admin: AdminUser, db: DbSession) -> DailyReportSweepResult:
    return await SchedulerService(db).send_daily_reports_sweep()


@router.post(
    "/clients/{client_id}/report-email/send",
    summary="Send one client's daily report email now, bypassing the 23:30 schedule (admin, for QA)",
)
async def send_client_report_email(client_id: uuid.UUID, admin: AdminUser, db: DbSession) -> dict:
    client = db.get(Client, client_id)
    if client is None:
        raise NotFoundError("Client not found.")
    tz = ZoneInfo(client.timezone) if client.timezone else ZoneInfo("UTC")
    report_date: date = datetime.now(UTC).astimezone(tz).date()
    log = await ReportEmailService(db).send_daily_report(client, report_date)
    return {"status": log.status, "report_date": log.report_date.isoformat()}
