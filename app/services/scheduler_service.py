"""Platform-wide scheduled operations — the automation layer over per-client
services. Turns the on-demand KPI watchdog and integration sync into sweeps
across every active client, and builds a deterministic daily digest.

Used by both the admin ``/automation`` endpoints (manual trigger) and the scheduler
process (``python -m app.scheduler``). Per-client failures are isolated so one
bad client never aborts a whole sweep. The digest is deterministic (no AI/token
cost) — it summarizes what already exists.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.client import Client
from app.models.enums import (
    AlertStatus,
    ClientStatus,
    IntegrationStatus,
    NotificationLevel,
)
from app.repositories.alert_repository import AlertRepository
from app.repositories.campaign_repository import CampaignRepository
from app.schemas.automation import (
    AlertBrief,
    ClientDigest,
    ClientSweepRow,
    DigestList,
    SyncSweepResult,
    SyncSweepRow,
    WatchdogSweepResult,
)
from app.services.alert_service import AlertService
from app.services.integration_service import _REAL_KEYS, IntegrationService
from app.services.notification_service import NotificationService

logger = logging.getLogger("app.scheduler")

_ONBOARDING_TOTAL_STEPS = 8
_DIGEST_TOP_ALERTS = 5


class SchedulerService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _active_clients(self) -> list[Client]:
        return list(
            self.db.scalars(
                select(Client)
                .where(Client.status == ClientStatus.active)
                .order_by(Client.created_at.asc())
            ).all()
        )

    # ---- KPI watchdog sweep ------------------------------------------- #

    def run_watchdog_sweep(self) -> WatchdogSweepResult:
        rows: list[ClientSweepRow] = []
        opened = updated = resolved = 0
        for client in self._active_clients():
            try:
                result = AlertService(self.db).evaluate(client.id)  # commits per client
            except Exception:
                logger.warning("Watchdog failed for client %s", client.id, exc_info=True)
                continue
            opened += result.opened
            updated += result.updated
            resolved += result.auto_resolved
            if result.opened or result.updated:
                # Surface it to the people tagged to this project (the "red dot").
                open_count = result.opened + result.updated
                NotificationService(self.db).notify_client_team(
                    client.id,
                    kind="alert",
                    level=NotificationLevel.warning,
                    title=f"{client.name}: {open_count} KPI alert(s) need attention",
                    body="Open the alerts view to acknowledge or resolve them.",
                    link=f"/clients/{client.id}/alerts",
                    rec_key=f"watchdog:{client.id}",
                )
            rows.append(
                ClientSweepRow(
                    client_id=client.id,
                    client_name=client.name,
                    opened=result.opened,
                    updated=result.updated,
                    auto_resolved=result.auto_resolved,
                )
            )
        return WatchdogSweepResult(
            clients=len(rows), opened=opened, updated=updated, auto_resolved=resolved,
            per_client=rows,
        )

    # ---- integration sync sweep --------------------------------------- #

    async def sync_integrations_sweep(self) -> SyncSweepResult:
        details: list[SyncSweepRow] = []
        synced = failed = 0
        for client in self._active_clients():
            service = IntegrationService(self.db)
            listing = service.list(client.id)
            for item in listing.items:
                if item.status != IntegrationStatus.connected or item.key not in _REAL_KEYS:
                    continue
                try:
                    await service.sync(client.id, item.key)
                    synced += 1
                    ok, err = True, None
                except Exception as exc:  # isolate per-integration failures
                    logger.warning(
                        "Sync failed: client=%s key=%s", client.id, item.key, exc_info=True
                    )
                    failed += 1
                    ok, err = False, str(exc)[:300]
                details.append(
                    SyncSweepRow(
                        client_id=client.id, client_name=client.name,
                        key=item.key.value, ok=ok, error=err,
                    )
                )
        return SyncSweepResult(
            clients=len(self._active_clients()), synced=synced, failed=failed, details=details
        )

    # ---- daily digest ------------------------------------------------- #

    def build_digest(self, client_id: uuid.UUID) -> ClientDigest:
        client = self.db.get(Client, client_id)
        if client is None:
            raise NotFoundError("Client not found.")
        return self._digest_for(client)

    def build_all_digests(self) -> DigestList:
        items = [self._digest_for(c) for c in self._active_clients()]
        return DigestList(items=items, total=len(items))

    def _digest_for(self, client: Client) -> ClientDigest:
        open_alerts, total_open = AlertRepository(self.db).list_for_client(
            client.id, status=AlertStatus.open.value, limit=None
        )
        by_sev = {"high": 0, "medium": 0, "low": 0}
        for a in open_alerts:
            by_sev[a.severity] = by_sev.get(a.severity, 0) + 1
        top = [
            AlertBrief(id=a.id, title=a.title, severity=a.severity, metric=a.metric)
            for a in _by_severity(open_alerts)[:_DIGEST_TOP_ALERTS]
        ]

        listing = IntegrationService(self.db).list(client.id)
        connected = [i.key.value for i in listing.items if i.status == IntegrationStatus.connected]
        pending = [i.key.value for i in listing.items if i.status != IntegrationStatus.connected]

        _rows, campaign_count = CampaignRepository(self.db).list_for_client(client.id, limit=None)

        step = client.onboarding_step or 1
        percent = int(step / _ONBOARDING_TOTAL_STEPS * 100 + 0.5)

        return ClientDigest(
            client_id=client.id,
            client_name=client.name,
            status=getattr(client.status, "value", client.status),
            onboarding_percent=min(percent, 100),
            campaign_count=campaign_count,
            open_alerts=total_open,
            high=by_sev["high"], medium=by_sev["medium"], low=by_sev["low"],
            top_alerts=top,
            connected_integrations=connected,
            pending_integrations=pending,
            generated_at=datetime.now(UTC),
        )


_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _by_severity(alerts: list) -> list:
    return sorted(alerts, key=lambda a: _SEVERITY_ORDER.get(a.severity, 9))
