"""Dashboard AI use-cases.

Assembles the client's real signals once, builds the intelligence context once,
then runs the four dashboard engines (health score, executive brief, watchdog,
recommendations) concurrently and merges in any recorded recommendation
decisions. Also owns the write path for accept/modify/reject decisions.

Client-access scoping is enforced at the router (``ClientService.get_client``)
before any method here runs. Repositories flush; this service owns the commit.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.dashboard_signals import DashboardSignals, GoalMetric
from app.ai.executive_brief import ExecutiveBriefAgent
from app.ai.features import AiFeature
from app.ai.health_score import HealthScoreAgent
from app.ai.opportunities import OpportunityDetector
from app.ai.qa import QAReviewer
from app.ai.recommendations import RecommendationsAgent
from app.ai.usage import AiUsageContext
from app.ai.watchdog import WatchdogAgent
from app.integrations.anthropic.client import AnthropicClient
from app.models.client import Client
from app.models.enums import ApprovalStatus, ComplianceKind, IntegrationStatus
from app.models.event import MarketingEvent
from app.models.recommendation import RecommendationAction
from app.repositories.recommendation_repository import RecommendationRepository
from app.schemas.ai import (
    DashboardResponse,
    ExecutiveBrief,
    OpportunityResponse,
    QAVerdict,
    Recommendation,
    RecommendationActionListResponse,
    RecommendationActionRead,
    RecommendationDecisionRead,
    RecommendationDecisionRequest,
)
from app.services.intelligence.context_service import ContextService


class DashboardService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.recommendations = RecommendationRepository(db)

    async def build(
        self, client: Client, *, user_id: uuid.UUID | None = None
    ) -> DashboardResponse:
        signals = self._signals(client)
        context = ContextService(self.db).build(client.id)

        def usage(feature: str) -> AiUsageContext:
            return AiUsageContext(feature=feature, client_id=client.id, user_id=user_id)

        health, brief, watchdog, recs = await asyncio.gather(
            HealthScoreAgent().generate(client, context, signals, usage(AiFeature.HEALTH_SCORE)),
            ExecutiveBriefAgent().generate(client, context, signals, usage(AiFeature.EXECUTIVE_BRIEF)),
            WatchdogAgent().generate(client, context, signals, usage(AiFeature.WATCHDOG)),
            RecommendationsAgent().generate(client, context, signals, usage(AiFeature.RECOMMENDATION)),
        )

        self._merge_decisions(client.id, recs)
        qa_review = await self._qa_review(brief, recs, context.preamble, signals, usage)
        return DashboardResponse(
            health_score=health,
            executive_brief=brief,
            watchdog=watchdog,
            recommendations=recs,
            ai_generated=AnthropicClient().is_configured,
            qa_review=qa_review,
        )

    async def _qa_review(
        self,
        brief: ExecutiveBrief,
        recs: list[Recommendation],
        preamble: str,
        signals: DashboardSignals,
        usage,
    ) -> QAVerdict:
        """Second-provider QA of the generated brief + recommendations.

        No-ops to a clean ``not_reviewed`` verdict when QA is disabled/unconfigured.
        """
        reviewer = QAReviewer()
        if not reviewer.is_enabled:
            return QAVerdict(status="not_reviewed")
        lines = [f"Executive brief headline: {brief.headline}"]
        lines += [f"- {r.title}: {r.summary} (impact: {r.expected_impact})" for r in recs]
        return await reviewer.review(
            content="\n".join(lines),
            content_label="dashboard brief + recommendations",
            preamble=preamble,
            facts=signals.as_prompt_facts(),
            usage=usage(AiFeature.QA_REVIEW),
        )

    async def opportunities(
        self, client: Client, *, user_id: uuid.UUID | None = None
    ) -> OpportunityResponse:
        """Growth opportunities grounded in the client's signals + external research."""
        signals = self._signals(client)
        context = ContextService(self.db).build(client.id)
        usage = AiUsageContext(
            feature=AiFeature.OPPORTUNITY, client_id=client.id, user_id=user_id
        )
        return await OpportunityDetector().detect(client, context, signals, usage)

    # ---- recommendation decisions ---- #

    def record_decision(
        self,
        client_id: uuid.UUID,
        rec_key: str,
        data: RecommendationDecisionRequest,
        *,
        decided_by: uuid.UUID,
    ) -> RecommendationAction:
        action = RecommendationAction(
            client_id=client_id,
            rec_key=rec_key,
            decision=data.decision,
            reason=data.reason,
            decided_by=decided_by,
        )
        self.recommendations.add(action)
        self.db.commit()
        self.db.refresh(action)
        return action

    def list_decisions(self, client_id: uuid.UUID) -> RecommendationActionListResponse:
        rows = self.recommendations.list_for_client(client_id)
        items = [RecommendationActionRead.model_validate(r) for r in rows]
        return RecommendationActionListResponse(items=items, total=len(items))

    # ---- helpers ---- #

    def _merge_decisions(
        self, client_id: uuid.UUID, recs: list[Recommendation]
    ) -> None:
        latest = self.recommendations.latest_by_rec_key(client_id)
        for rec in recs:
            action = latest.get(rec.id)
            if action is not None:
                rec.decision = RecommendationDecisionRead.model_validate(action)

    def _signals(self, client: Client) -> DashboardSignals:
        integrations = list(client.integrations)
        connected = sum(1 for i in integrations if i.status == IntegrationStatus.connected)
        pending = len(integrations) - connected

        pending_approvals = self.db.scalar(
            select(func.count())
            .select_from(MarketingEvent)
            .where(
                MarketingEvent.client_id == client.id,
                MarketingEvent.approval_status == ApprovalStatus.pending,
            )
        ) or 0

        def _texts(kind: ComplianceKind) -> list[str]:
            return [
                e.text for e in client.compliance_entries
                if getattr(e.kind, "value", e.kind) == kind.value and e.is_active
            ]

        campaigns = list(client.campaigns)
        return DashboardSignals(
            spend=float(client.spend_total),
            leads=client.leads_total,
            cpl=float(client.cpl),
            connected_integrations=connected,
            pending_integrations=pending,
            pending_approvals=int(pending_approvals),
            onboarding_completed=client.onboarding_step >= 8,
            has_profile=client.current_profile_version is not None,
            banned_words=_texts(ComplianceKind.banned),
            required_phrases=_texts(ComplianceKind.required),
            rules=_texts(ComplianceKind.rule),
            platforms=[p.channel for p in client.platforms],
            goals=client.goals,
            brand_voice=client.brand_voice,
            active_campaigns=sum(1 for c in campaigns if c.status == "active"),
            goal_metrics=_goal_metrics(campaigns),
        )


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _goal_metrics(campaigns: list) -> list[GoalMetric]:
    """Cross-campaign, cross-channel goal-relative KPIs: client targets vs actuals.

    Actuals are rolled up across ALL campaigns (project-level, not per-platform);
    targets are the average of the non-null KPI targets the client agreed to. Only
    KPIs that have both a target and enough actual data to compute are returned.
    """
    if not campaigns:
        return []

    impressions = sum(int(c.impressions or 0) for c in campaigns)
    clicks = sum(int(c.clicks or 0) for c in campaigns)
    conversions = sum(int(c.conversions or 0) for c in campaigns)
    leads = sum(int(c.leads or 0) for c in campaigns)
    spend = sum(float(c.spend or 0) for c in campaigns)

    target_cpl = _avg([float(c.target_cpl) for c in campaigns if c.target_cpl is not None])
    target_ctr = _avg([float(c.target_ctr) for c in campaigns if c.target_ctr is not None])
    target_cvr = _avg(
        [float(c.target_conversion_rate) for c in campaigns if c.target_conversion_rate is not None]
    )

    metrics: list[GoalMetric] = []
    # CPL — lower is better (needs spend + leads to be meaningful).
    if target_cpl is not None and leads > 0:
        actual_cpl = spend / leads
        metrics.append(GoalMetric(
            label="Cost per lead vs target", target=round(target_cpl, 2),
            actual=round(actual_cpl, 2), higher_is_better=False,
            on_track=actual_cpl <= target_cpl, unit="$",
        ))
    # CTR (%) — higher is better.
    if target_ctr is not None and impressions > 0:
        actual_ctr = clicks / impressions * 100
        metrics.append(GoalMetric(
            label="Click-through rate vs target", target=round(target_ctr, 3),
            actual=round(actual_ctr, 3), higher_is_better=True,
            on_track=actual_ctr >= target_ctr, unit="%",
        ))
    # Conversion rate (%) — higher is better.
    if target_cvr is not None and clicks > 0:
        actual_cvr = conversions / clicks * 100
        metrics.append(GoalMetric(
            label="Conversion rate vs target", target=round(target_cvr, 3),
            actual=round(actual_cvr, 3), higher_is_better=True,
            on_track=actual_cvr >= target_cvr, unit="%",
        ))
    return metrics
