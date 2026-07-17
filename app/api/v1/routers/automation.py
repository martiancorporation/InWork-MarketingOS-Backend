"""Platform automation API (v1) — admin-only cross-client automation triggers.

- ``POST /automation/watchdog/run``        — run the KPI watchdog across all active clients
- ``POST /automation/integrations/sync``   — sync every connected integration
- ``GET  /automation/digest``              — daily digest for all active clients
- ``GET  /automation/clients/{id}/digest`` — daily digest for one client

These are platform-wide operations, so they require an administrator. The same
service methods are driven on a cadence by the scheduler process
(``python -m app.scheduler``).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import AdminUser, DbSession
from app.schemas.automation import (
    ClientDigest,
    DigestList,
    SyncSweepResult,
    WatchdogSweepResult,
)
from app.services.scheduler_service import SchedulerService

router = APIRouter(prefix="/automation", tags=["automation"])


@router.post(
    "/watchdog/run",
    response_model=WatchdogSweepResult,
    summary="Run the KPI watchdog across all active clients (admin)",
)
def run_watchdog(admin: AdminUser, db: DbSession) -> WatchdogSweepResult:
    return SchedulerService(db).run_watchdog_sweep()


@router.post(
    "/integrations/sync",
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
