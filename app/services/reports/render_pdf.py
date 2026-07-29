"""Render a :class:`ReportContent` as a PDF, using ``reportlab`` (the one new
pinned dependency this feature needed — no PDF library existed in this repo).

Table cells are passed as plain strings/numbers (reportlab renders them as-is,
no markup parsing), so no escaping is needed there. ``Paragraph`` *does* parse a
small HTML-like markup, so the one place free text goes through it (the
went-right/went-wrong narrative, built from campaign names in ``content.py``) is
escaped first — not a security boundary, just correctness against a stray
``<``/``&`` in a campaign name.
"""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.reports.content import CampaignRow, ChannelRow, ReportContent

_SECTION_TITLES = {
    "campaign_performance": "Campaign Performance",
    "ga_overview": "Channel Overview",
    "top_ads": "Top-Performing Campaigns",
    "went_wrong_right": "What Went Right / Wrong",
}

_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F2")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
)


def _campaign_table(rows: list[CampaignRow]) -> Table:
    header = ["Campaign", "Status", "Spend", "Leads", "CPL", "Target CPL", "CTR %", "Target CTR %"]
    data = [header] + [
        [
            c.name,
            c.status,
            f"${c.spend:,.2f}",
            c.leads,
            f"${c.cpl:,.2f}",
            f"${c.target_cpl:,.2f}" if c.target_cpl else "—",
            f"{c.ctr:.1f}%",
            f"{c.target_ctr:.1f}%" if c.target_ctr else "—",
        ]
        for c in rows
    ] or [header, ["No campaigns in this period.", "", "", "", "", "", "", ""]]
    table = Table(data, repeatRows=1)
    table.setStyle(_TABLE_STYLE)
    return table


def _channel_table(rows: list[ChannelRow], totals) -> Table:
    header = [
        "Channel",
        "Impressions",
        "Clicks",
        "Conv.",
        "Leads",
        "Spend",
        "Revenue",
        "CTR %",
        "CPL",
        "ROAS",
    ]
    data = [header]
    for row in rows:
        t = row.totals
        data.append(
            [
                row.label,
                t.impressions,
                t.clicks,
                t.conversions,
                t.leads,
                f"${t.spend:,.2f}",
                f"${t.revenue:,.2f}",
                f"{t.ctr:.1f}%",
                f"${t.cpl:,.2f}",
                f"{t.roas:.2f}x",
            ]
        )
    if not rows:
        data.append(["No channel data in this period.", "", "", "", "", "", "", "", "", ""])
    data.append(
        [
            "Total",
            totals.impressions,
            totals.clicks,
            totals.conversions,
            totals.leads,
            f"${totals.spend:,.2f}",
            f"${totals.revenue:,.2f}",
            f"{totals.ctr:.1f}%",
            f"${totals.cpl:,.2f}",
            f"{totals.roas:.2f}x",
        ]
    )
    table = Table(data, repeatRows=1)
    table.setStyle(_TABLE_STYLE)
    return table


def render_pdf(content: ReportContent) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"{escape(content.client_name)} — Report", styles["Title"]),
        Paragraph(
            f"{content.date_from.isoformat()} to {content.date_to.isoformat()}", styles["Normal"]
        ),
        Paragraph(
            f"Channels: {escape(', '.join(content.channel_labels) or '(none)')}", styles["Normal"]
        ),
        Spacer(1, 0.25 * inch),
    ]

    for section in content.included_sections:
        story.append(Paragraph(_SECTION_TITLES[section], styles["Heading2"]))
        if section == "campaign_performance":
            story.append(_campaign_table(content.campaigns))
        elif section == "top_ads":
            story.append(_campaign_table(content.top_campaigns))
        elif section == "ga_overview":
            story.append(_channel_table(content.channel_breakdown, content.totals))
        elif section == "went_wrong_right":
            story.append(
                Paragraph(f"<b>What went right:</b> {escape(content.went_right)}", styles["Normal"])
            )
            story.append(Spacer(1, 0.1 * inch))
            story.append(
                Paragraph(f"<b>What went wrong:</b> {escape(content.went_wrong)}", styles["Normal"])
            )
        story.append(Spacer(1, 0.3 * inch))

    doc.build(story)
    return buf.getvalue()
