"""Assembles the data a generated report file renders from.

Content is driven by ``sections`` + ``channels`` from the request — not by
``kind``/``scope``, which are presentational labels only (the report title).
This one axis covers every UI preset (full/leads/meta) and custom with no
special-casing per preset.

Deliberately has no AI dependency: ``went_right``/``went_wrong`` are computed
directly from the campaign rows already pulled for this same report, not routed
through ``ExecutiveBriefAgent``/``DashboardService`` (that machinery assembles the
full live-dashboard signal set + an async AI call, built for a different job).
Keeping this deterministic means report generation is fast and always available,
Anthropic configured or not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.client import Client
from app.models.enums import IntegrationKey, SocialPlatform
from app.repositories.analytics_repository import AnalyticsRepository
from app.repositories.campaign_repository import CampaignRepository
from app.schemas.analytics import AnalyticsTotals
from app.services.analytics_service import AnalyticsService

ALL_SECTION_KEYS = ("campaign_performance", "ga_overview", "top_ads", "went_wrong_right")

# Which SocialPlatform bucket(s) an analytics_daily row lands in for a given
# IntegrationKey — mirrors the sync write path (integration_service.py's Meta
# sync writes under SocialPlatform.facebook today; Instagram is included too as
# a harmless superset in case that sync is ever split out).
_CHANNEL_PLATFORMS: dict[IntegrationKey, tuple[SocialPlatform, ...]] = {
    IntegrationKey.meta: (SocialPlatform.facebook, SocialPlatform.instagram),
    IntegrationKey.google_ads: (SocialPlatform.google,),
    IntegrationKey.google_lsa: (SocialPlatform.google_lsa,),
    IntegrationKey.ga4: (SocialPlatform.ga4,),
    IntegrationKey.search_console: (SocialPlatform.seo,),
}

_CHANNEL_LABELS: dict[IntegrationKey, str] = {
    IntegrationKey.meta: "Meta",
    IntegrationKey.google_ads: "Google Ads",
    IntegrationKey.google_lsa: "Google LSA",
    IntegrationKey.ga4: "GA4",
    IntegrationKey.search_console: "Search Console",
}

_PLATFORM_TO_CHANNEL: dict[SocialPlatform, IntegrationKey] = {
    platform: channel for channel, platforms in _CHANNEL_PLATFORMS.items() for platform in platforms
}


@dataclass
class ChannelRow:
    label: str
    totals: AnalyticsTotals


@dataclass
class CampaignRow:
    name: str
    status: str
    spend: float
    leads: int
    cpl: float
    target_cpl: float | None
    ctr: float
    target_ctr: float | None


@dataclass
class ReportContent:
    client_name: str
    date_from: date
    date_to: date
    channel_labels: list[str]
    totals: AnalyticsTotals
    channel_breakdown: list[ChannelRow] = field(default_factory=list)
    campaigns: list[CampaignRow] = field(default_factory=list)
    top_campaigns: list[CampaignRow] = field(default_factory=list)
    went_right: str = ""
    went_wrong: str = ""
    included_sections: tuple[str, ...] = ALL_SECTION_KEYS


def build_report_content(
    db: Session,
    client: Client,
    *,
    date_from: date,
    date_to: date,
    channels: list[str] | None,
    sections: list[str] | None,
) -> ReportContent:
    selected_channels = _resolve_channels(channels) or list(_CHANNEL_PLATFORMS)
    platforms = {p for ch in selected_channels for p in _CHANNEL_PLATFORMS[ch]}

    raw_rows = [
        r
        for r in AnalyticsRepository(db).by_platform(client.id, start=date_from, end=date_to)
        if r["platform"] in platforms
    ]
    channel_breakdown = _group_by_channel(raw_rows)
    totals = _sum_rows(raw_rows) if raw_rows else AnalyticsTotals()

    all_campaigns, _ = CampaignRepository(db).list_for_client(client.id, limit=None)
    campaigns = [_to_campaign_row(c) for c in all_campaigns if _overlaps(c, date_from, date_to)]
    top_campaigns = sorted(campaigns, key=lambda c: c.leads, reverse=True)[:5]
    went_right, went_wrong = _went_right_wrong(campaigns)

    requested = tuple(s for s in (sections or ALL_SECTION_KEYS) if s in ALL_SECTION_KEYS)

    return ReportContent(
        client_name=client.name,
        date_from=date_from,
        date_to=date_to,
        channel_labels=[_CHANNEL_LABELS[c] for c in selected_channels],
        totals=totals,
        channel_breakdown=channel_breakdown,
        campaigns=campaigns,
        top_campaigns=top_campaigns,
        went_right=went_right,
        went_wrong=went_wrong,
        included_sections=requested or ALL_SECTION_KEYS,
    )


def _resolve_channels(channels: list[str] | None) -> list[IntegrationKey]:
    if not channels:
        return []
    resolved: list[IntegrationKey] = []
    for raw in channels:
        try:
            key = IntegrationKey(raw)
        except ValueError:
            continue  # untyped input at the schema edge — skip, don't 500
        if key in _CHANNEL_PLATFORMS and key not in resolved:
            resolved.append(key)
    return resolved


def _sum_rows(rows: list[dict]) -> AnalyticsTotals:
    agg = {
        m: sum((r[m] for r in rows), start=0)
        for m in ("impressions", "clicks", "conversions", "leads", "spend", "revenue")
    }
    return AnalyticsService._totals(agg)  # noqa: SLF001 - shared formula, not a layering violation


def _group_by_channel(rows: list[dict]) -> list[ChannelRow]:
    by_label: dict[str, list[dict]] = {}
    for row in rows:
        channel = _PLATFORM_TO_CHANNEL.get(row["platform"])
        label = _CHANNEL_LABELS[channel] if channel else str(row["platform"].value)
        by_label.setdefault(label, []).append(row)
    return [ChannelRow(label=label, totals=_sum_rows(group)) for label, group in by_label.items()]


def _to_campaign_row(campaign: Campaign) -> CampaignRow:
    spend = float(campaign.spend)
    leads = int(campaign.leads)
    clicks = int(campaign.clicks)
    impressions = int(campaign.impressions)
    return CampaignRow(
        name=campaign.name,
        status=campaign.status,
        spend=spend,
        leads=leads,
        cpl=round(spend / leads, 2) if leads else 0.0,
        target_cpl=float(campaign.target_cpl) if campaign.target_cpl is not None else None,
        ctr=round(clicks / impressions * 100, 2) if impressions else 0.0,
        target_ctr=float(campaign.target_ctr) if campaign.target_ctr is not None else None,
    )


def _overlaps(campaign: Campaign, date_from: date, date_to: date) -> bool:
    # A campaign with no explicit dates is treated as always relevant, matching
    # how dateless items are handled elsewhere in this codebase (never excluded).
    start = campaign.start_date or date_from
    end = campaign.end_date or date_to
    return start <= date_to and end >= date_from


def _went_right_wrong(campaigns: list[CampaignRow]) -> tuple[str, str]:
    if not campaigns:
        return (
            "No campaign activity in this period.",
            "No campaign activity in this period.",
        )

    best = max(campaigns, key=lambda c: c.leads)
    right = f'"{best.name}" led with {best.leads} leads at ${best.cpl:,.2f} CPL' + (
        f" (target ${best.target_cpl:,.2f})." if best.target_cpl else "."
    )

    over_target = [c for c in campaigns if c.target_cpl and c.cpl > c.target_cpl]
    zero_lead_spend = [c for c in campaigns if c.leads == 0 and c.spend > 0]
    if over_target:
        worst = max(over_target, key=lambda c: c.cpl - (c.target_cpl or 0))
        wrong = (
            f'"{worst.name}" ran ${worst.cpl:,.2f} CPL against a ${worst.target_cpl:,.2f} target.'
        )
    elif zero_lead_spend:
        worst = max(zero_lead_spend, key=lambda c: c.spend)
        wrong = f'"{worst.name}" spent ${worst.spend:,.2f} with no leads recorded.'
    else:
        wrong = "No campaigns missed their targets in this period."

    return right, wrong
