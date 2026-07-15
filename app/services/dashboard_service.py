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

from app.ai.dashboard_signals import DashboardSignals
from app.ai.executive_brief import ExecutiveBriefAgent
from app.ai.features import AiFeature
from app.ai.health_score import HealthScoreAgent
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
        return DashboardResponse(
            health_score=health,
            executive_brief=brief,
            watchdog=watchdog,
            recommendations=recs,
            ai_generated=AnthropicClient().is_configured,
        )

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
        )
