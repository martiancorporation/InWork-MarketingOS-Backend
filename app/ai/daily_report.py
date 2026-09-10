"""Daily report narrative engine.

Writes the short "what happened today" commentary (headline, highlights,
watch-outs, recommended actions) that sits on top of the deterministic
numbers assembled by ``app/services/report_email/data.py``. Modeled 1:1 on
``ExecutiveBriefAgent``: AI provider when configured, deterministic fallback
from the same facts otherwise — the AI never invents a number that isn't
already in ``DailyReportData``.
"""

from __future__ import annotations

import logging

from app.ai.features import AiFeature
from app.ai.model_router import model_for
from app.ai.parsers import parse_json_object
from app.ai.usage import AiUsageContext
from app.integrations.llm import LLMClient, get_llm_client
from app.prompts.loader import load_prompt, render
from app.schemas.ai import DailyReportNarrative
from app.services.report_email.data import DailyReportData

logger = logging.getLogger("app.ai.daily_report")


class DailyReportAgent:
    feature = AiFeature.REPORT_NARRATIVE

    def __init__(self, ai_client: LLMClient | None = None) -> None:
        self._client = ai_client or get_llm_client()

    async def generate(
        self, data: DailyReportData, usage: AiUsageContext | None = None
    ) -> DailyReportNarrative:
        if not self._client.is_configured:
            return self._fallback(data)
        try:
            system = load_prompt("daily_report/system.txt")
            prompt = render(
                load_prompt("daily_report/user_template.txt"),
                {
                    "client_name": data.client.name or "the client",
                    "report_date": data.report_date.isoformat(),
                    "facts": _facts_text(data),
                },
            )
            raw = await self._client.complete(
                system=system,
                prompt=prompt,
                max_tokens=1200,
                model=model_for(self.feature),
                context=usage,
            )
            return DailyReportNarrative.model_validate(parse_json_object(raw))
        except Exception:
            logger.warning(
                "Daily report AI narrative failed for client %s", data.client.id, exc_info=True
            )
            return self._fallback(data)

    def _fallback(self, data: DailyReportData) -> DailyReportNarrative:
        t = data.content.totals
        headline = (
            f"{data.client.name}: {t.leads} lead(s) at ${t.cpl:,.2f} CPL from "
            f"${t.spend:,.2f} spend on {data.report_date.isoformat()}."
        )
        highlights = [data.content.went_right] if data.content.went_right else []
        watch_outs = [data.content.went_wrong] if data.content.went_wrong else []
        if data.pending_integrations:
            watch_outs.append(f"Not connected: {', '.join(data.pending_integrations)}.")
        actions: list[str] = []
        if data.alerts.open_total:
            actions.append(f"Review {data.alerts.open_total} open alert(s).")
        if data.pipeline.pending_approval_today:
            actions.append(
                f"{data.pipeline.pending_approval_today} calendar item(s) awaiting client approval today."
            )
        if not actions:
            actions.append("No blocking actions — keep monitoring performance.")
        return DailyReportNarrative(
            headline=headline,
            highlights=highlights,
            watch_outs=watch_outs,
            recommended_actions=actions,
        )


def _facts_text(data: DailyReportData) -> str:
    t = data.content.totals
    lines = [
        f"- Date: {data.report_date.isoformat()}",
        f"- Spend: ${t.spend:,.2f}, Leads: {t.leads}, CPL: ${t.cpl:,.2f}",
        f"- Impressions: {t.impressions}, Clicks: {t.clicks}, Conversions: {t.conversions}",
        f"- Connected integrations: {', '.join(data.connected_integrations) or 'none'}",
        f"- Not connected: {', '.join(data.pending_integrations) or 'none'}",
        f"- Open alerts: {data.alerts.open_total} (high={data.alerts.high}, "
        f"medium={data.alerts.medium}, low={data.alerts.low})",
        f"- Calendar today: {data.pipeline.published_today} published, "
        f"{data.pipeline.scheduled_today} scheduled, {data.pipeline.draft_today} draft, "
        f"{data.pipeline.pending_approval_today} awaiting client approval",
        f"- What went right (deterministic): {data.content.went_right}",
        f"- What went wrong (deterministic): {data.content.went_wrong}",
    ]
    channel_rows = data.content.channel_breakdown + data.extra_channels
    if channel_rows:
        lines.append("- By channel:")
        for row in channel_rows:
            lines.append(
                f"    - {row.label}: spend ${row.totals.spend:,.2f}, leads {row.totals.leads}, "
                f"CPL ${row.totals.cpl:,.2f}"
            )
    return "\n".join(lines)
