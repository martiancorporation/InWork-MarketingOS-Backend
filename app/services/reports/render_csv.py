"""Render a :class:`ReportContent` as one CSV file.

A CSV can't hold multiple sheets, so each requested section becomes its own
block within the one file: a ``### <Section Title>`` marker row, a header row,
then data rows, then a blank separator. Any spreadsheet tool opens this as a
single sheet; it isn't meant to be parsed back programmatically.
"""

from __future__ import annotations

import csv
import io

from app.services.reports.content import ReportContent

_SECTION_TITLES = {
    "campaign_performance": "Campaign Performance",
    "ga_overview": "Channel Overview",
    "top_ads": "Top-Performing Campaigns",
    "went_wrong_right": "What Went Right / Wrong",
}

_CAMPAIGN_HEADER = [
    "Campaign",
    "Status",
    "Spend",
    "Leads",
    "CPL",
    "Target CPL",
    "CTR %",
    "Target CTR %",
]
_CHANNEL_HEADER = [
    "Channel",
    "Impressions",
    "Clicks",
    "Conversions",
    "Leads",
    "Spend",
    "Revenue",
    "CTR %",
    "CPL",
    "ROAS",
]


def render_csv(content: ReportContent) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow([f"{content.client_name} — Report"])
    writer.writerow([f"{content.date_from.isoformat()} to {content.date_to.isoformat()}"])
    writer.writerow([f"Channels: {', '.join(content.channel_labels) or '(none)'}"])
    writer.writerow([])

    for section in content.included_sections:
        writer.writerow([f"### {_SECTION_TITLES[section]}"])
        if section in ("campaign_performance", "top_ads"):
            rows = content.campaigns if section == "campaign_performance" else content.top_campaigns
            writer.writerow(_CAMPAIGN_HEADER)
            if not rows:
                writer.writerow(["No campaigns in this period."])
            for c in rows:
                writer.writerow(
                    [
                        c.name,
                        c.status,
                        c.spend,
                        c.leads,
                        c.cpl,
                        c.target_cpl or "",
                        c.ctr,
                        c.target_ctr or "",
                    ]
                )
        elif section == "ga_overview":
            writer.writerow(_CHANNEL_HEADER)
            if not content.channel_breakdown:
                writer.writerow(["No channel data in this period."])
            for row in content.channel_breakdown:
                t = row.totals
                writer.writerow(
                    [
                        row.label,
                        t.impressions,
                        t.clicks,
                        t.conversions,
                        t.leads,
                        t.spend,
                        t.revenue,
                        t.ctr,
                        t.cpl,
                        t.roas,
                    ]
                )
            t = content.totals
            writer.writerow(
                [
                    "Total",
                    t.impressions,
                    t.clicks,
                    t.conversions,
                    t.leads,
                    t.spend,
                    t.revenue,
                    t.ctr,
                    t.cpl,
                    t.roas,
                ]
            )
        elif section == "went_wrong_right":
            writer.writerow(["What went right", content.went_right])
            writer.writerow(["What went wrong", content.went_wrong])
        writer.writerow([])

    return buf.getvalue().encode("utf-8")
