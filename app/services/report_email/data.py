"""Assembles everything the daily report email needs for one client/day.

Reuses the two heaviest-lifting pieces almost verbatim rather than
re-deriving them:

- ``build_report_content`` (``app/services/reports/content.py``) for
  per-channel performance totals, campaign rows, and Meta campaign/issue
  detail — called with ``date_from == date_to == report_date`` for "today".
- The same ``AlertRepository``/``IntegrationService`` calls
  ``SchedulerService._digest_for`` already makes for the daily digest, for
  open-alerts-by-severity and connected/pending integrations. Not imported
  from ``SchedulerService`` directly — that module calls into this package to
  run the sweep, and importing back would create a cycle; the few lines
  duplicated here are the same ones already used there.

Connection status stays a separate field rather than being folded into
``content``: the renderer decides to show "Not connected" for a pending
integration's channel, instead of ``build_report_content``'s ordinary
(correct, for other callers) zero-filled row.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.enums import (
    AlertStatus,
    ApprovalStatus,
    EventStage,
    IntegrationStatus,
    SocialPlatform,
)
from app.models.event import MarketingEvent
from app.repositories.alert_repository import AlertRepository
from app.repositories.analytics_repository import AnalyticsRepository
from app.services.analytics_service import AnalyticsService
from app.services.integration_service import IntegrationService
from app.services.reports.content import ChannelRow, ReportContent, build_report_content

_TOP_ALERTS = 5
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class AlertBrief:
    id: uuid.UUID
    title: str
    severity: str
    metric: str | None


@dataclass
class AlertsSummary:
    open_total: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    top: list[AlertBrief] = field(default_factory=list)


@dataclass
class ContentPipelineSnapshot:
    """Calendar activity for the report date, grouped by production stage."""

    draft_today: int = 0
    scheduled_today: int = 0
    published_today: int = 0
    pending_approval_today: int = 0


@dataclass
class DailyReportData:
    client: Client
    report_date: date
    connected_integrations: list[str]
    pending_integrations: list[str]
    alerts: AlertsSummary
    content: ReportContent
    # LinkedIn isn't in build_report_content's channel map (app/services/reports/content.py
    # only covers meta/google_ads/google_lsa/ga4/search_console), so its analytics_daily
    # row is pulled separately here rather than silently dropped from the email.
    extra_channels: list[ChannelRow] = field(default_factory=list)
    pipeline: ContentPipelineSnapshot = field(default_factory=ContentPipelineSnapshot)


def build_daily_report_data(db: Session, client: Client, report_date: date) -> DailyReportData:
    listing = IntegrationService(db).list(client.id)
    connected = [i.key.value for i in listing.items if i.status == IntegrationStatus.connected]
    pending = [i.key.value for i in listing.items if i.status != IntegrationStatus.connected]

    content = build_report_content(
        db, client, date_from=report_date, date_to=report_date, channels=None, sections=None
    )
    linkedin_row = _linkedin_channel_row(db, client.id, report_date)

    return DailyReportData(
        client=client,
        report_date=report_date,
        connected_integrations=connected,
        pending_integrations=pending,
        alerts=_alerts_summary(db, client.id),
        content=content,
        extra_channels=[linkedin_row] if linkedin_row else [],
        pipeline=_pipeline_snapshot(db, client.id, report_date),
    )


def _linkedin_channel_row(
    db: Session, client_id: uuid.UUID, report_date: date
) -> ChannelRow | None:
    rows = [
        r
        for r in AnalyticsRepository(db).by_platform(client_id, start=report_date, end=report_date)
        if r["platform"] == SocialPlatform.linkedin
    ]
    if not rows:
        return None
    agg = {
        m: sum((r[m] for r in rows), start=0)
        for m in ("impressions", "clicks", "conversions", "leads", "spend", "revenue")
    }
    return ChannelRow(label="LinkedIn", totals=AnalyticsService._totals(agg))  # noqa: SLF001 - shared formula, same as content.py's _sum_rows


def _alerts_summary(db: Session, client_id: uuid.UUID) -> AlertsSummary:
    open_alerts, total_open = AlertRepository(db).list_for_client(
        client_id, status=AlertStatus.open.value, limit=None
    )
    by_sev = {"high": 0, "medium": 0, "low": 0}
    for a in open_alerts:
        by_sev[a.severity] = by_sev.get(a.severity, 0) + 1
    ranked = sorted(open_alerts, key=lambda a: _SEVERITY_ORDER.get(a.severity, 9))
    top = [
        AlertBrief(id=a.id, title=a.title, severity=a.severity, metric=a.metric)
        for a in ranked[:_TOP_ALERTS]
    ]
    return AlertsSummary(
        open_total=total_open,
        high=by_sev["high"],
        medium=by_sev["medium"],
        low=by_sev["low"],
        top=top,
    )


def _pipeline_snapshot(
    db: Session, client_id: uuid.UUID, report_date: date
) -> ContentPipelineSnapshot:
    rows = db.execute(
        select(MarketingEvent.stage, func.count())
        .where(MarketingEvent.client_id == client_id, MarketingEvent.event_date == report_date)
        .group_by(MarketingEvent.stage)
    ).all()
    counts = {getattr(stage, "value", stage): count for stage, count in rows}

    pending_approval = (
        db.scalar(
            select(func.count())
            .select_from(MarketingEvent)
            .where(
                MarketingEvent.client_id == client_id,
                MarketingEvent.event_date == report_date,
                MarketingEvent.approval_status == ApprovalStatus.pending,
            )
        )
        or 0
    )

    return ContentPipelineSnapshot(
        draft_today=counts.get(EventStage.draft.value, 0),
        scheduled_today=counts.get(EventStage.scheduled.value, 0),
        published_today=counts.get(EventStage.published.value, 0),
        pending_approval_today=int(pending_approval),
    )
