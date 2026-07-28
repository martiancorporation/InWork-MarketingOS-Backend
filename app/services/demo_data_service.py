"""Synthetic demo data, so every client's screens render before connectors exist.

Per the 27-Jul direction, reporting always reads from our own database and never
live from a third-party API. Until ad-platform credentials land there is nothing
to read, so this service fills a client's tables with plausible data — the whole
pipeline (aggregation, charts, health scores, alerts) is then exercised end to end
against real queries rather than mocked responses.

Two entry points share this one implementation:

* ``OnboardingService`` calls :meth:`seed_client` when a client is created, so a
  brand-new client is never a set of empty panels (gated by ``DEMO_SEED_ON_CREATE``).
* ``scripts/seed_synthetic_analytics.py`` calls it in bulk, to backfill clients
  that already existed.

Everything written here is identifiable and reversible: analytics rows carry
``source='synthetic'``, and the other records use the fixed titles below. Because
``source`` sits outside the ``(client_id, date, platform)`` natural key, a real
connector sync overwrites a synthetic cell in place and re-stamps it — nothing has
to be cleaned up first.

Numbers derive from ``random.Random(client.slug)``, so a given client always gets
the same figures: re-running is a no-op, and a demo looks the same twice.
"""

from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass
from datetime import date, time, timedelta

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.models.analytics import AnalyticsDaily
from app.models.campaign import Campaign
from app.models.client import Client
from app.models.enums import (
    AdObjective,
    AnalyticsSource,
    ApprovalStatus,
    CampaignStatus,
    EventStage,
    EventType,
    SocialPlatform,
    TaskCategory,
    TaskStatus,
)
from app.models.event import MarketingEvent
from app.models.plan import PlanTask
from app.schemas.analytics import AnalyticsDailyIn
from app.services.client_rollup import refresh_client_rollups

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 90


@dataclass(frozen=True)
class _Profile:
    """A per-platform performance shape. ``cpc=0`` means an organic channel."""

    platform: SocialPlatform
    impressions: int  # typical daily impressions (or sessions, for ga4)
    ctr: float  # click-through rate, percent
    cvr: float  # click → conversion rate, percent
    cpc: float  # cost per click, USD (0 = organic, no spend)
    aov: float  # revenue per conversion, USD


# A believable cross-channel mix: paid search carries the spend, Meta the volume,
# LinkedIn the low-volume/high-value B2B leads, GA4/SEO the organic side.
# Rates are deliberately in industry-normal territory — per-channel ROAS lands
# around 3-5x, because a demo showing 15x reads as obviously fabricated.
_PROFILES = (
    _Profile(SocialPlatform.google, impressions=4200, ctr=3.4, cvr=3.8, cpc=2.35, aov=280.0),
    _Profile(SocialPlatform.facebook, impressions=6800, ctr=1.9, cvr=2.6, cpc=1.15, aov=180.0),
    _Profile(SocialPlatform.linkedin, impressions=1400, ctr=0.8, cvr=2.4, cpc=5.40, aov=900.0),
    _Profile(SocialPlatform.ga4, impressions=3100, ctr=8.5, cvr=2.2, cpc=0.0, aov=150.0),
    _Profile(SocialPlatform.seo, impressions=2400, ctr=5.2, cvr=1.9, cpc=0.0, aov=165.0),
)

# name, objective, status, share of the client's totals, target bias, lead efficiency.
#
# ``target_bias`` (<1 = target easier than actual → healthy; >1 = missed → needs
# attention) spreads the health scores and goal-completion bars.
#
# ``efficiency`` scales leads independently of spend, which is what makes the
# campaigns genuinely differ. A plain proportional split gives every campaign the
# same spend-to-leads ratio, so cost per lead comes out identical and "best" vs
# "worst campaign" is decided by rounding noise — search converts best, awareness
# worst, which is the ordinary shape.
_CAMPAIGN_SPECS = (
    ("Always-On Search", AdObjective.leads, CampaignStatus.active, 0.45, 0.88, 1.35),
    ("Social Prospecting", AdObjective.traffic, CampaignStatus.active, 0.35, 1.14, 0.95),
    ("Brand Awareness Push", AdObjective.awareness, CampaignStatus.paused, 0.20, 1.00, 0.55),
)

# Plan items, as day offsets from today, chosen to exercise every colour the
# calendar can render: overdue (red), done (green), in-flight and upcoming
# (orange), a month-long campaign span, and one undated backlog item.
_PLAN_SPECS: tuple[tuple[str, TaskCategory, TaskStatus, int | None, int | None], ...] = (
    ("Always-on search campaign", TaskCategory.ads, TaskStatus.in_progress, -20, 10),
    ("Publish monthly case study", TaskCategory.content, TaskStatus.done, -6, -6),
    ("Draft quarterly blog post", TaskCategory.content, TaskStatus.todo, -3, -3),
    ("LinkedIn thought-leadership post", TaskCategory.creative, TaskStatus.todo, 2, 2),
    ("Refresh ad creatives", TaskCategory.creative, TaskStatus.blocked, 5, 7),
    ("Monthly performance review", TaskCategory.analytics, TaskStatus.todo, 9, 9),
    ("Backlog: audit landing pages", TaskCategory.admin, TaskStatus.todo, None, None),
)

# Calendar items. Two sit at `pending` so the dashboard's approval queue and the
# Approvals screen both have something real to act on.
_EVENT_SPECS: tuple[tuple[str, EventType, SocialPlatform, int, ApprovalStatus, EventStage], ...] = (
    (
        "Product launch teaser",
        EventType.content,
        SocialPlatform.linkedin,
        3,
        ApprovalStatus.pending,
        EventStage.draft,
    ),
    (
        "Q3 retargeting ad set",
        EventType.ad,
        SocialPlatform.facebook,
        5,
        ApprovalStatus.pending,
        EventStage.draft,
    ),
    (
        "Monthly newsletter",
        EventType.email,
        SocialPlatform.email,
        -4,
        ApprovalStatus.approved,
        EventStage.published,
    ),
)


def _day_factor(rnd: random.Random, day: date, index: int, total: int, *, paid: bool) -> float:
    """Weekend dip + gentle growth over the window + day-to-day jitter."""
    weekend = day.weekday() >= 5
    dip = (0.72 if paid else 0.86) if weekend else 1.0
    growth = 1.0 + 0.25 * (index / total)  # ~25% growth across the window
    return dip * growth * rnd.uniform(0.85, 1.15)


def _quantize(rnd: random.Random, value: float) -> int:
    """Round to an int, carrying the fraction as a probability.

    Plain ``int()`` truncation zeroes out low-volume channels — LinkedIn at
    ~0.3 conversions/day would report zero conversions for the whole window,
    making its CPL undefined and the channel look broken. Rounding
    probabilistically keeps period totals honest at daily granularity.
    """
    whole = int(value)
    return whole + (1 if rnd.random() < (value - whole) else 0)


def rows_for_client(slug: str, days: int, today: date) -> list[AnalyticsDailyIn]:
    """Generate one daily fact per (platform, day). Deterministic for a slug."""
    rnd = random.Random(slug)
    rows: list[AnalyticsDailyIn] = []
    for profile in _PROFILES:
        scale = rnd.uniform(0.7, 1.4)  # this client's size relative to the profile
        for i in range(days):
            day = today - timedelta(days=days - 1 - i)
            factor = _day_factor(rnd, day, i, days, paid=profile.cpc > 0)
            impressions = max(0, int(profile.impressions * scale * factor))
            clicks = _quantize(rnd, impressions * profile.ctr / 100 * rnd.uniform(0.9, 1.1))
            conversions = _quantize(rnd, clicks * profile.cvr / 100 * rnd.uniform(0.85, 1.15))
            # Leads run a little ahead of conversions (form fills that don't close).
            leads = conversions + _quantize(rnd, conversions * rnd.uniform(0.15, 0.5))
            rows.append(
                AnalyticsDailyIn(
                    date=day,
                    platform=profile.platform,
                    impressions=impressions,
                    clicks=clicks,
                    conversions=conversions,
                    leads=leads,
                    spend=round(clicks * profile.cpc, 2),
                    revenue=round(conversions * profile.aov * rnd.uniform(0.8, 1.25), 2),
                )
            )
    return rows


@dataclass
class SeedResult:
    """What a single client's seed actually wrote."""

    analytics_rows: int = 0
    campaigns: int = 0
    plan_tasks: int = 0
    events: int = 0
    alerts: int = 0
    protected_cells: int = 0
    skipped: bool = False

    @property
    def wrote_anything(self) -> bool:
        return bool(self.analytics_rows or self.campaigns or self.plan_tasks or self.events)


class DemoDataService:
    """Fills a client's tables with synthetic-but-plausible records."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- queries -------------------------------------------------------- #

    def has_analytics(self, client_id: uuid.UUID) -> bool:
        return bool(
            self.db.scalar(
                select(func.count())
                .select_from(AnalyticsDaily)
                .where(AnalyticsDaily.client_id == client_id)
            )
        )

    def _protected_cells(self, client_id: uuid.UUID) -> set[tuple[date, str]]:
        """(date, platform) cells already holding real data, which must not move.

        Anything not tagged ``synthetic`` came from a connector sync or an operator
        CSV import — it is measured fact, and placeholder data must never clobber
        it. The upsert keys on (client, date, platform), so those cells are skipped.
        """
        rows = self.db.execute(
            select(AnalyticsDaily.date, AnalyticsDaily.platform).where(
                AnalyticsDaily.client_id == client_id,
                AnalyticsDaily.source.is_distinct_from(AnalyticsSource.synthetic.value),
            )
        ).all()
        return {(day, getattr(platform, "value", platform)) for day, platform in rows}

    # ---- writes --------------------------------------------------------- #

    def seed_client(
        self,
        client: Client,
        *,
        days: int = DEFAULT_DAYS,
        today: date | None = None,
        actor_id: uuid.UUID | None = None,
        skip_if_present: bool = False,
    ) -> SeedResult:
        """Seed one client. Caller owns the surrounding transaction boundary.

        ``skip_if_present`` is what the create-time hook uses: if a client somehow
        already has analytics, leave it entirely alone.
        """
        today = today or date.today()
        result = SeedResult()

        if skip_if_present and self.has_analytics(client.id):
            result.skipped = True
            return result

        result.analytics_rows, result.protected_cells = self._seed_analytics(client, days, today)
        rows = rows_for_client(client.slug, days, today)
        result.campaigns = self._seed_campaigns(client, rows, today, days)
        result.plan_tasks = self._seed_plan_tasks(client, today, actor_id)
        result.events = self._seed_events(client, today, actor_id)
        # Bulk insert bypasses AnalyticsService.ingest, so refresh the rollups here.
        refresh_client_rollups(self.db, client.id)
        return result

    def _seed_analytics(self, client: Client, days: int, today: date) -> tuple[int, int]:
        rows = rows_for_client(client.slug, days, today)
        protected = self._protected_cells(client.id)
        if protected:
            kept = [r for r in rows if (r.date, r.platform.value) not in protected]
            skipped = len(rows) - len(kept)
            rows = kept
        else:
            skipped = 0

        # Delete-then-insert our own rows rather than upserting one at a time: the
        # per-row SELECT in AnalyticsService.ingest is ~900 round trips for a 90-day
        # seed, which is seconds against a remote database and far too slow to sit
        # inside an HTTP request. Only synthetic rows are removed.
        self.db.query(AnalyticsDaily).filter(
            AnalyticsDaily.client_id == client.id,
            AnalyticsDaily.source == AnalyticsSource.synthetic.value,
        ).delete(synchronize_session=False)

        if rows:
            self.db.execute(
                insert(AnalyticsDaily),
                [
                    {
                        "id": uuid.uuid4(),
                        "client_id": client.id,
                        "date": r.date,
                        "platform": r.platform,
                        "impressions": r.impressions,
                        "clicks": r.clicks,
                        "conversions": r.conversions,
                        "leads": r.leads,
                        "spend": r.spend,
                        "revenue": r.revenue,
                        "source": AnalyticsSource.synthetic.value,
                    }
                    for r in rows
                ],
            )
        return len(rows), skipped

    def _seed_campaigns(
        self, client: Client, rows: list[AnalyticsDailyIn], today: date, days: int
    ) -> int:
        """Upsert campaigns by (client, name), with actuals consistent with the facts."""
        totals = {
            "impressions": sum(r.impressions for r in rows),
            "clicks": sum(r.clicks for r in rows),
            "conversions": sum(r.conversions for r in rows),
            "leads": sum(r.leads for r in rows),
            "spend": sum(r.spend for r in rows),
            "revenue": sum(r.revenue for r in rows),
        }
        touched = 0
        for name, objective, status, share, target_bias, efficiency in _CAMPAIGN_SPECS:
            impressions = int(totals["impressions"] * share)
            clicks = int(totals["clicks"] * share)
            # Efficiency moves conversions/leads without moving spend, so each
            # campaign lands at a genuinely different cost per lead.
            conversions = int(totals["conversions"] * share * efficiency)
            leads = int(totals["leads"] * share * efficiency)
            spend = round(totals["spend"] * share, 2)
            revenue = round(totals["revenue"] * share, 2)

            actual_cpl = (spend / leads) if leads else 0.0
            actual_ctr = (clicks / impressions * 100) if impressions else 0.0
            actual_cvr = (conversions / clicks * 100) if clicks else 0.0

            existing = self.db.scalar(
                select(Campaign).where(Campaign.client_id == client.id, Campaign.name == name)
            )
            campaign = existing or Campaign(client_id=client.id, name=name)
            campaign.objective = objective.value
            campaign.status = status.value
            campaign.start_date = today - timedelta(days=days - 1)
            campaign.end_date = (
                today + timedelta(days=30) if status == CampaignStatus.active else today
            )
            campaign.budget_usd = round(spend * 1.2, 2)
            # A target below actual CPL means the campaign is overspending per lead;
            # target_bias tunes each so the health scores aren't all identical.
            campaign.target_cpl = round(actual_cpl / target_bias, 2) if actual_cpl else None
            campaign.target_ctr = round(actual_ctr * target_bias, 3) if actual_ctr else None
            campaign.target_conversion_rate = (
                round(actual_cvr * target_bias, 3) if actual_cvr else None
            )
            campaign.impressions = impressions
            campaign.clicks = clicks
            campaign.conversions = conversions
            campaign.leads = leads
            campaign.spend = spend
            campaign.revenue = revenue
            if existing is None:
                self.db.add(campaign)
            touched += 1
        return touched

    def _seed_plan_tasks(self, client: Client, today: date, actor_id: uuid.UUID | None) -> int:
        """Fill the Plan board / calendar widget. Idempotent by (client, title)."""
        existing = set(
            self.db.scalars(select(PlanTask.title).where(PlanTask.client_id == client.id)).all()
        )
        added = 0
        for title, category, status, start_off, due_off in _PLAN_SPECS:
            if title in existing:
                continue
            self.db.add(
                PlanTask(
                    client_id=client.id,
                    title=title,
                    category=category,
                    status=status,
                    start_date=today + timedelta(days=start_off) if start_off is not None else None,
                    due_date=today + timedelta(days=due_off) if due_off is not None else None,
                    start_time=time(9, 30) if start_off is not None else None,
                    end_time=time(17, 0) if start_off is not None else None,
                    assignee_id=actor_id,
                    created_by=actor_id,
                )
            )
            added += 1
        return added

    def _seed_events(self, client: Client, today: date, actor_id: uuid.UUID | None) -> int:
        """Fill the approval queue. Idempotent by (client, title)."""
        existing = set(
            self.db.scalars(
                select(MarketingEvent.title).where(MarketingEvent.client_id == client.id)
            ).all()
        )
        added = 0
        for title, kind, platform, day_off, approval, stage in _EVENT_SPECS:
            if title in existing:
                continue
            self.db.add(
                MarketingEvent(
                    client_id=client.id,
                    title=title,
                    type=kind,
                    platform=platform,
                    event_date=today + timedelta(days=day_off),
                    event_time=time(10, 0),
                    stage=stage,
                    approval_status=approval,
                    created_by=actor_id,
                )
            )
            added += 1
        return added

    # ---- teardown ------------------------------------------------------- #

    @staticmethod
    def seed_detached(client_id: uuid.UUID, actor_id: uuid.UUID | None = None) -> None:
        """Seed on a fresh session, for use as a FastAPI background task.

        Runs *after* the response is sent. Seeding costs roughly a dozen database
        round trips, which is a couple of seconds on a nearby database and over ten
        against a distant one — far too much to hang off the wizard's first step.
        The operator has seven more steps to fill in before any dashboard is opened,
        so landing the data a moment later is invisible.

        Own session (the request's is already closed), and errors are swallowed:
        sample data must never surface as a failure on a client that was created
        successfully. Mirrors how the audit middleware isolates itself.
        """
        from app.core.config import get_settings
        from app.db.session import get_session_factory
        from app.services.alert_service import AlertService

        settings = get_settings()
        try:
            with get_session_factory()() as db:
                client = db.get(Client, client_id)
                if client is None:
                    return
                service = DemoDataService(db)
                result = service.seed_client(
                    client,
                    days=settings.demo.seed_days,
                    actor_id=actor_id,
                    skip_if_present=True,
                )
                if result.skipped:
                    return
                db.commit()
                # Alerts are derived from the seeded campaigns rather than invented,
                # so the watchdog shows what a real evaluation would.
                AlertService(db).evaluate(client_id)
        except Exception:  # pragma: no cover - never surface as a request failure
            logger.exception("background demo seed failed for client %s", client_id)

    def purge_client(self, client: Client) -> int:
        """Remove synthetic analytics rows only. Connector/CSV data is untouched."""
        return (
            self.db.query(AnalyticsDaily)
            .filter(
                AnalyticsDaily.client_id == client.id,
                AnalyticsDaily.source == AnalyticsSource.synthetic.value,
            )
            .delete(synchronize_session=False)
            or 0
        )
