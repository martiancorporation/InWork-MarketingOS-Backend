"""Platform-wide scheduled operations — the automation layer over per-client
services. Turns the on-demand KPI watchdog and integration sync into sweeps
across every active client, and builds a deterministic daily digest.

Used by both the admin ``/automation`` endpoints (manual trigger) and the scheduler
process (``python -m app.scheduler``). Per-client failures are isolated so one
bad client never aborts a whole sweep. The digest is deterministic (no AI/token
cost) — it summarizes what already exists.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import anyio
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.client import Client
from app.models.enums import (
    AlertStatus,
    ClientStatus,
    IntegrationStatus,
    NotificationLevel,
)
from app.repositories.alert_repository import AlertRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.session_repository import SessionRepository
from app.schemas.alert import AlertEvaluateResult
from app.schemas.automation import (
    AlertBrief,
    ClientDigest,
    ClientSweepRow,
    DailyReportSweepResult,
    DailyReportSweepRow,
    DigestList,
    SyncSweepResult,
    SyncSweepRow,
    WatchdogSweepResult,
)
from app.services.alert_service import AlertService
from app.services.dashboard_service import DashboardService
from app.services.integration_service import _REAL_KEYS, IntegrationService
from app.services.notification_service import NotificationService
from app.services.report_email.service import ReportEmailService
from app.services.report_email.timing import due_report_dates

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

    def _new_session(self) -> Session:
        """A fresh, independent ``Session`` on the same engine as ``self.db``.

        Used by the concurrent sweeps below so each in-flight client gets its
        own session — a ``Session`` must never be shared across concurrently
        running tasks. Binding to ``self.db``'s own engine (rather than a
        separate ``get_session_factory()``) means this works identically in
        production (the real app engine) and in tests (the per-test SQLite
        engine the ``db_session`` fixture creates) — the same pattern
        ``tests/integration/test_worker.py`` uses for the intelligence worker.
        """
        return Session(bind=self.db.get_bind())

    def _sweep_semaphore(self) -> asyncio.Semaphore:
        """Bounds how many clients a sweep processes at once.

        Postgres only. Every sweep gives each in-flight client its own
        ``Session``, which on SQLite all share one StaticPool connection —
        interleaving transactions on a single DBAPI connection (and, for the
        watchdog, from several OS threads). Only a real connection pool
        supports that, so tests and local tooling run these strictly
        sequentially.
        """
        concurrency = (
            get_settings().scheduler.sweep_concurrency
            if self.db.get_bind().dialect.name == "postgresql"
            else 1
        )
        return asyncio.Semaphore(concurrency)

    # ---- KPI watchdog sweep ------------------------------------------- #

    async def run_watchdog_sweep(self) -> WatchdogSweepResult:
        """Evaluate every active client's alerts, ``sweep_concurrency`` at a time.

        Each concurrent unit of work opens its own DB session — a SQLAlchemy
        ``Session`` is not safe to share across concurrently-running tasks, even
        on a single-threaded event loop, since two tasks could otherwise
        interleave mid-flush. ``AlertService.evaluate`` is itself synchronous
        (DB-bound, not network-bound), so it runs in a worker thread via
        ``anyio.to_thread.run_sync`` — that's what actually lets N clients'
        evaluations overlap instead of blocking the loop one at a time.
        """
        clients = self._active_clients()
        semaphore = self._sweep_semaphore()

        def _evaluate_one(client_id: uuid.UUID, client_name: str) -> AlertEvaluateResult | None:
            """Returns ``None`` if this client failed — logged here, in the
            worker thread, where the traceback is still live."""
            session = self._new_session()
            try:
                result = AlertService(session).evaluate(client_id)  # commits per client
                if result.opened or result.updated:
                    open_count = result.opened + result.updated
                    NotificationService(session).notify_client_team(
                        client_id,
                        kind="alert",
                        level=NotificationLevel.warning,
                        title=f"{client_name}: {open_count} KPI alert(s) need attention",
                        body="Open the alerts view to acknowledge or resolve them.",
                        link=f"/clients/{client_id}/alerts",
                        rec_key=f"watchdog:{client_id}",
                    )
                return result
            except Exception:  # isolate per-client failures
                logger.warning("Watchdog failed for client %s", client_id, exc_info=True)
                return None
            finally:
                session.close()

        async def _one(client: Client) -> ClientSweepRow | None:
            async with semaphore:
                result = await anyio.to_thread.run_sync(_evaluate_one, client.id, client.name)
            if result is None:
                return None
            return ClientSweepRow(
                client_id=client.id,
                client_name=client.name,
                opened=result.opened,
                updated=result.updated,
                auto_resolved=result.auto_resolved,
            )

        results = await asyncio.gather(*(_one(c) for c in clients))
        rows = [r for r in results if r is not None]
        return WatchdogSweepResult(
            clients=len(rows),
            opened=sum(r.opened for r in rows),
            updated=sum(r.updated for r in rows),
            auto_resolved=sum(r.auto_resolved for r in rows),
            per_client=rows,
        )

    # ---- integration sync sweep --------------------------------------- #

    async def sync_integrations_sweep(self) -> SyncSweepResult:
        """Sync every connected integration, ``sweep_concurrency`` clients at a
        time. Each concurrent client gets its own DB session (see
        ``run_watchdog_sweep`` for why); per-integration failures are still
        isolated exactly as before.

        After a client's integrations finish syncing, this also pre-warms its
        dashboard (health score / brief / watchdog / recommendations) in the
        background — see ``_prewarm_dashboard`` — so a user opening the
        dashboard later reads an already-fresh ``DashboardSnapshot`` instead of
        paying for live AI calls on page load. A client with nothing connected
        never reaches that call at all (``rows`` stays empty).
        """
        clients = self._active_clients()
        semaphore = self._sweep_semaphore()

        async def _one(client: Client) -> list[SyncSweepRow]:
            rows: list[SyncSweepRow] = []
            async with semaphore:
                session = self._new_session()
                try:
                    service = IntegrationService(session)
                    listing = service.list(client.id)
                    for item in listing.items:
                        if item.status != IntegrationStatus.connected or item.key not in _REAL_KEYS:
                            continue
                        try:
                            await service.sync(client.id, item.key)
                            ok, err = True, None
                        except Exception as exc:  # isolate per-integration failures
                            logger.warning(
                                "Sync failed: client=%s key=%s", client.id, item.key, exc_info=True
                            )
                            ok, err = False, str(exc)[:300]
                        rows.append(
                            SyncSweepRow(
                                client_id=client.id,
                                client_name=client.name,
                                key=item.key.value,
                                ok=ok,
                                error=err,
                            )
                        )
                    if rows:  # only clients with at least one real connected integration
                        await self._prewarm_dashboard(session, client)
                finally:
                    session.close()
            return rows

        results = await asyncio.gather(*(_one(c) for c in clients))
        details = [row for rows in results for row in rows]
        synced = sum(1 for row in details if row.ok)
        failed = sum(1 for row in details if not row.ok)
        return SyncSweepResult(clients=len(clients), synced=synced, failed=failed, details=details)

    async def _prewarm_dashboard(self, session: Session, client: Client) -> None:
        """Recompute ``client``'s dashboard snapshot in the background if
        anything the AI engines read has actually changed.

        ``DashboardService.build`` already hashes its inputs and skips the
        4-6 AI calls entirely when nothing changed since the last snapshot —
        so on the common "synced, nothing new" tick this is a cheap no-op,
        and on a real change it's exactly the AI work a dashboard page-load
        would otherwise have to do live. Isolated in its own try/except: one
        client's AI failure must never break the sync sweep's own reporting.
        """
        try:
            fresh_client = session.get(Client, client.id)
            if fresh_client is not None:
                await DashboardService(session).build(fresh_client)
        except Exception:
            logger.warning("Dashboard pre-warm failed for client %s", client.id, exc_info=True)

    # ---- expired session purge ---------------------------------------- #

    def purge_expired_sessions(self) -> int:
        """Delete every ``user_sessions`` row past its expiry. Nothing else ever
        cleans this table up (logout deletes one row; expiry alone never did),
        so left unswept it grows by one row per login forever."""
        deleted = SessionRepository(self.db).purge_expired(now=datetime.now(UTC))
        if deleted:
            self.db.commit()
        return deleted

    # ---- audit-log retention -------------------------------------------- #

    def purge_expired_audit_logs(self) -> int:
        """Delete ``audit_log`` rows older than the configured retention
        window. Every API request is logged here with no other cleanup path,
        so left unswept it grows forever."""
        retention_days = get_settings().scheduler.audit_log_retention_days
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        deleted = AuditRepository(self.db).purge_older_than(cutoff)
        if deleted:
            self.db.commit()
        return deleted

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
            high=by_sev["high"],
            medium=by_sev["medium"],
            low=by_sev["low"],
            top_alerts=top,
            connected_integrations=connected,
            pending_integrations=pending,
            generated_at=datetime.now(UTC),
        )

    # ---- daily report email sweep -------------------------------------- #

    async def send_daily_reports_sweep(self) -> DailyReportSweepResult:
        """Send the daily report email to every active client whose local
        23:30 threshold has passed and hasn't been sent yet today (or, as a
        bounded catch-up, yesterday). Isolated per client/date exactly like
        ``sync_integrations_sweep``, and ``sweep_concurrency`` clients run at
        once — each on its own DB session, since sending involves a real
        AI provider call plus a Brevo send per client/date and shouldn't
        serialize across the whole client base."""
        clients = self._active_clients()
        semaphore = self._sweep_semaphore()
        now_utc = datetime.now(UTC)

        async def _one(client: Client) -> list[DailyReportSweepRow]:
            rows: list[DailyReportSweepRow] = []
            due_dates = due_report_dates(client.timezone, now_utc)
            if not due_dates:
                return rows
            async with semaphore:
                session = self._new_session()
                try:
                    fresh_client = session.get(Client, client.id)
                    if fresh_client is None:
                        return rows
                    for report_date in due_dates:
                        try:
                            log = await ReportEmailService(session).send_daily_report(
                                fresh_client, report_date
                            )
                        except Exception as exc:  # isolate per-client/date failures
                            logger.warning(
                                "Daily report sweep failed: client=%s date=%s",
                                client.id,
                                report_date,
                                exc_info=True,
                            )
                            rows.append(
                                DailyReportSweepRow(
                                    client_id=client.id,
                                    client_name=client.name,
                                    report_date=report_date.isoformat(),
                                    status="error",
                                    error=str(exc)[:300],
                                )
                            )
                            continue
                        rows.append(
                            DailyReportSweepRow(
                                client_id=client.id,
                                client_name=client.name,
                                report_date=report_date.isoformat(),
                                status=log.status,
                                error=log.error,
                            )
                        )
                finally:
                    session.close()
            return rows

        results = await asyncio.gather(*(_one(c) for c in clients))
        rows = [row for client_rows in results for row in client_rows]
        sent = sum(1 for row in rows if row.status == "sent")
        failed = sum(1 for row in rows if row.status in ("error", "failed"))
        skipped = len(rows) - sent - failed
        return DailyReportSweepResult(
            clients=len(clients),
            sent=sent,
            skipped=skipped,
            failed=failed,
            details=rows,
        )


_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _by_severity(alerts: list) -> list:
    return sorted(alerts, key=lambda a: _SEVERITY_ORDER.get(a.severity, 9))
