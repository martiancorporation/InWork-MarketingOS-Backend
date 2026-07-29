"""Render a :class:`ReportContent` as a single flattened .jpg image.

Mirrors ``app/utils/render.py``'s Playwright usage (same launch args, same
async API), but renders a small **self-contained** HTML string built entirely
from our own data via ``page.set_content`` — no navigation to any URL, so none
of that module's SSRF-guard machinery applies here. Every interpolated value is
``html.escape()``-d before going into the template; this isn't a security
boundary (nothing here is served back as live HTML, only rasterized to a JPEG),
it's just correctness against a stray ``<``/``&`` in a client or campaign name.
"""

from __future__ import annotations

from html import escape

from app.services.reports.content import CampaignRow, ChannelRow, ReportContent

_LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

_SECTION_TITLES = {
    "campaign_performance": "Campaign Performance",
    "ga_overview": "Channel Overview",
    "top_ads": "Top-Performing Campaigns",
    "went_wrong_right": "What Went Right / Wrong",
}

_STYLE = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; color: #18181B; margin: 32px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.meta { color: #52525B; font-size: 13px; margin-bottom: 24px; }
h2 { font-size: 15px; margin: 24px 0 8px; border-bottom: 1px solid #E4E4E7; padding-bottom: 4px; }
table { border-collapse: collapse; width: 100%; font-size: 12px; }
th, td { border: 1px solid #E4E4E7; padding: 6px 8px; text-align: left; }
th { background: #F4F4F5; font-weight: 600; }
.narrative { font-size: 13px; line-height: 1.6; }
.narrative b { color: #18181B; }
"""


def _campaign_table(rows: list[CampaignRow]) -> str:
    header = "".join(
        f"<th>{h}</th>"
        for h in [
            "Campaign",
            "Status",
            "Spend",
            "Leads",
            "CPL",
            "Target CPL",
            "CTR %",
            "Target CTR %",
        ]
    )
    if not rows:
        body = '<tr><td colspan="8">No campaigns in this period.</td></tr>'
    else:
        body = "".join(
            "<tr>"
            f"<td>{escape(c.name)}</td><td>{escape(c.status)}</td>"
            f"<td>${c.spend:,.2f}</td><td>{c.leads}</td><td>${c.cpl:,.2f}</td>"
            f"<td>{f'${c.target_cpl:,.2f}' if c.target_cpl else '—'}</td>"
            f"<td>{c.ctr:.1f}%</td>"
            f"<td>{f'{c.target_ctr:.1f}%' if c.target_ctr else '—'}</td>"
            "</tr>"
            for c in rows
        )
    return f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def _channel_table(rows: list[ChannelRow], totals) -> str:
    header = "".join(
        f"<th>{h}</th>"
        for h in [
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
    )
    body_rows = [
        "<tr>"
        f"<td>{escape(row.label)}</td><td>{row.totals.impressions}</td><td>{row.totals.clicks}</td>"
        f"<td>{row.totals.conversions}</td><td>{row.totals.leads}</td>"
        f"<td>${row.totals.spend:,.2f}</td><td>${row.totals.revenue:,.2f}</td>"
        f"<td>{row.totals.ctr:.1f}%</td><td>${row.totals.cpl:,.2f}</td><td>{row.totals.roas:.2f}x</td>"
        "</tr>"
        for row in rows
    ]
    if not rows:
        body_rows.append('<tr><td colspan="10">No channel data in this period.</td></tr>')
    body_rows.append(
        "<tr>"
        f"<td><b>Total</b></td><td>{totals.impressions}</td><td>{totals.clicks}</td>"
        f"<td>{totals.conversions}</td><td>{totals.leads}</td>"
        f"<td>${totals.spend:,.2f}</td><td>${totals.revenue:,.2f}</td>"
        f"<td>{totals.ctr:.1f}%</td><td>${totals.cpl:,.2f}</td><td>{totals.roas:.2f}x</td>"
        "</tr>"
    )
    return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _build_html(content: ReportContent) -> str:
    sections_html = []
    for section in content.included_sections:
        title = _SECTION_TITLES[section]
        if section == "campaign_performance":
            body = _campaign_table(content.campaigns)
        elif section == "top_ads":
            body = _campaign_table(content.top_campaigns)
        elif section == "ga_overview":
            body = _channel_table(content.channel_breakdown, content.totals)
        else:  # went_wrong_right
            body = (
                f'<p class="narrative"><b>What went right:</b> {escape(content.went_right)}</p>'
                f'<p class="narrative"><b>What went wrong:</b> {escape(content.went_wrong)}</p>'
            )
        sections_html.append(f"<h2>{title}</h2>{body}")

    channels = escape(", ".join(content.channel_labels) or "(none)")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>{_STYLE}</style></head>
<body>
<h1>{escape(content.client_name)} — Report</h1>
<div class="meta">{content.date_from.isoformat()} to {content.date_to.isoformat()} · Channels: {channels}</div>
{"".join(sections_html)}
</body></html>"""


async def render_visual(content: ReportContent) -> bytes:
    from playwright.async_api import async_playwright

    html = _build_html(content)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        try:
            page = await browser.new_page(viewport={"width": 1000, "height": 800})
            await page.set_content(html, wait_until="load")
            return await page.screenshot(type="jpeg", quality=85, full_page=True)
        finally:
            await browser.close()
