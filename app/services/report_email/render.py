"""Renders the daily report email body — plain string composition (no
templating dependency; Jinja2 isn't pinned and nothing else in this codebase
uses one), table-based layout with inline CSS for email-client compatibility.
"""

from __future__ import annotations

from html import escape

from app.schemas.ai import DailyReportNarrative
from app.services.report_email.data import DailyReportData
from app.services.reports.content import ChannelRow

# Every integration key the report covers, in display order. Broader than
# content.py's channel map (which omits LinkedIn) — this is the single place
# that decides what a reader sees per channel: numbers, "Not connected", or
# "connected, no data synced yet".
_KEY_LABELS: dict[str, str] = {
    "meta": "Meta",
    "google_ads": "Google Ads",
    "google_lsa": "Google LSA",
    "ga4": "GA4",
    "search_console": "Search Console",
    "linkedin": "LinkedIn",
}

_BODY_STYLE = "font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1a1a1a;"
_TABLE_STYLE = "width:100%;border-collapse:collapse;margin:8px 0 20px;"
_TH_STYLE = "text-align:left;padding:6px 10px;border-bottom:2px solid #ddd;font-size:12px;color:#666;text-transform:uppercase;"
_TD_STYLE = "padding:6px 10px;border-bottom:1px solid #eee;font-size:14px;"
_SECTION_TITLE_STYLE = "font-size:16px;margin:24px 0 4px;color:#111;"


def render_daily_report_html(data: DailyReportData, narrative: DailyReportNarrative) -> str:
    parts = [
        f'<div style="{_BODY_STYLE}max-width:680px;margin:0 auto;">',
        _header(data),
        _narrative_section(narrative),
        _performance_section(data),
        _campaigns_section(data),
        _alerts_section(data),
        _pipeline_section(data),
        _footer(),
        "</div>",
    ]
    return "\n".join(parts)


def _header(data: DailyReportData) -> str:
    return (
        f'<h1 style="font-size:20px;margin:0 0 2px;">{escape(data.client.name)} — Daily Report</h1>'
        f'<p style="color:#666;font-size:13px;margin:0 0 20px;">{data.report_date.isoformat()} · Internal use only</p>'
    )


def _narrative_section(narrative: DailyReportNarrative) -> str:
    def _list(items: list[str]) -> str:
        if not items:
            return '<p style="font-size:14px;color:#888;">None.</p>'
        lis = "".join(f"<li>{escape(i)}</li>" for i in items)
        return f'<ul style="font-size:14px;margin:4px 0;padding-left:20px;">{lis}</ul>'

    return (
        f'<p style="font-size:15px;font-weight:600;margin:0 0 12px;">{escape(narrative.headline)}</p>'
        f'<h2 style="{_SECTION_TITLE_STYLE}">Highlights</h2>{_list(narrative.highlights)}'
        f'<h2 style="{_SECTION_TITLE_STYLE}">Watch-outs</h2>{_list(narrative.watch_outs)}'
        f'<h2 style="{_SECTION_TITLE_STYLE}">Recommended actions</h2>{_list(narrative.recommended_actions)}'
    )


def _performance_section(data: DailyReportData) -> str:
    by_label: dict[str, ChannelRow] = {
        row.label: row for row in (*data.content.channel_breakdown, *data.extra_channels)
    }
    rows_html = []
    for key, label in _KEY_LABELS.items():
        if key in data.pending_integrations:
            rows_html.append(
                f"<tr><td style='{_TD_STYLE}'>{escape(label)}</td>"
                f"<td colspan='5' style='{_TD_STYLE}color:#999;'>Not connected</td></tr>"
            )
            continue
        row = by_label.get(label)
        if row is None:
            rows_html.append(
                f"<tr><td style='{_TD_STYLE}'>{escape(label)}</td>"
                f"<td colspan='5' style='{_TD_STYLE}color:#999;'>Connected — no data synced yet</td></tr>"
            )
            continue
        t = row.totals
        rows_html.append(
            f"<tr><td style='{_TD_STYLE}'>{escape(label)}</td>"
            f"<td style='{_TD_STYLE}'>${t.spend:,.2f}</td>"
            f"<td style='{_TD_STYLE}'>{t.impressions:,}</td>"
            f"<td style='{_TD_STYLE}'>{t.clicks:,}</td>"
            f"<td style='{_TD_STYLE}'>{t.leads:,}</td>"
            f"<td style='{_TD_STYLE}'>${t.cpl:,.2f}</td></tr>"
        )
    return (
        f'<h2 style="{_SECTION_TITLE_STYLE}">Performance by channel</h2>'
        f'<table style="{_TABLE_STYLE}"><thead><tr>'
        f"<th style='{_TH_STYLE}'>Channel</th><th style='{_TH_STYLE}'>Spend</th>"
        f"<th style='{_TH_STYLE}'>Impressions</th><th style='{_TH_STYLE}'>Clicks</th>"
        f"<th style='{_TH_STYLE}'>Leads</th><th style='{_TH_STYLE}'>CPL</th>"
        f"</tr></thead><tbody>{''.join(rows_html)}</tbody></table>"
    )


def _campaigns_section(data: DailyReportData) -> str:
    campaigns = data.content.top_campaigns
    if not campaigns:
        return ""
    rows_html = "".join(
        f"<tr><td style='{_TD_STYLE}'>{escape(c.name)}</td>"
        f"<td style='{_TD_STYLE}'>{escape(c.status)}</td>"
        f"<td style='{_TD_STYLE}'>${c.spend:,.2f}</td>"
        f"<td style='{_TD_STYLE}'>{c.leads:,}</td>"
        f"<td style='{_TD_STYLE}'>${c.cpl:,.2f}</td></tr>"
        for c in campaigns
    )
    return (
        f'<h2 style="{_SECTION_TITLE_STYLE}">Top campaigns</h2>'
        f'<table style="{_TABLE_STYLE}"><thead><tr>'
        f"<th style='{_TH_STYLE}'>Campaign</th><th style='{_TH_STYLE}'>Status</th>"
        f"<th style='{_TH_STYLE}'>Spend</th><th style='{_TH_STYLE}'>Leads</th>"
        f"<th style='{_TH_STYLE}'>CPL</th></tr></thead><tbody>{rows_html}</tbody></table>"
    )


def _alerts_section(data: DailyReportData) -> str:
    a = data.alerts
    summary = (
        f"{a.open_total} open (high {a.high} · medium {a.medium} · low {a.low})"
        if a.open_total
        else "No open alerts."
    )
    top_html = "".join(f"<li>[{escape(t.severity)}] {escape(t.title)}</li>" for t in a.top)
    return (
        f'<h2 style="{_SECTION_TITLE_STYLE}">Alerts</h2>'
        f'<p style="font-size:14px;margin:4px 0;">{summary}</p>'
        + (
            f'<ul style="font-size:14px;margin:4px 0;padding-left:20px;">{top_html}</ul>'
            if top_html
            else ""
        )
    )


def _pipeline_section(data: DailyReportData) -> str:
    p = data.pipeline
    return (
        f'<h2 style="{_SECTION_TITLE_STYLE}">Content pipeline — today</h2>'
        f'<p style="font-size:14px;margin:4px 0;">'
        f"Published: {p.published_today} · Scheduled: {p.scheduled_today} · "
        f"Draft: {p.draft_today} · Awaiting client approval: {p.pending_approval_today}</p>"
    )


def _footer() -> str:
    return (
        '<p style="font-size:11px;color:#999;margin-top:28px;border-top:1px solid #eee;padding-top:10px;">'
        "Automated daily report — internal distribution only. Do not forward to the client."
        "</p>"
    )
