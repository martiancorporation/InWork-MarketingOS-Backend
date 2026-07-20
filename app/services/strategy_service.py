"""Strategy-adherence use-cases (BE-06).

Records the AI-given strategy the operator signs off on (immutable, versioned per
client) and computes — deterministically — how closely they then followed it,
from recommendation decisions and plan-task completion.

Client-access scoping is enforced at the router (via ``ClientService.get_client``)
before any method here runs. Repositories flush; this service owns the commit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.strategy import Strategy
from app.repositories.plan_repository import PlanTaskRepository
from app.repositories.recommendation_repository import RecommendationRepository
from app.repositories.strategy_repository import StrategyRepository
from app.schemas.strategy import AdherenceSummary, StrategyCreate


class StrategyService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.strategies = StrategyRepository(db)
        self.recommendations = RecommendationRepository(db)
        self.tasks = PlanTaskRepository(db)

    def set_strategy(
        self, client_id: uuid.UUID, data: StrategyCreate, *, signed_by: uuid.UUID
    ) -> Strategy:
        """Record a new current strategy (a fresh version) for the client."""
        strategy = Strategy(
            client_id=client_id,
            version=self.strategies.next_version(client_id),
            title=data.title,
            content=data.content,
            signed_by=signed_by,
        )
        self.strategies.add(strategy)
        self.strategies.flush()  # assign id/version before returning
        self.db.commit()
        self.db.refresh(strategy)
        return strategy

    def get_current(self, client_id: uuid.UUID) -> Strategy:
        strategy = self.strategies.get_current(client_id)
        if strategy is None:
            raise NotFoundError("No strategy has been recorded for this client.")
        return strategy

    def adherence(self, client_id: uuid.UUID) -> AdherenceSummary:
        """Deterministic adherence summary for the client.

        Blends two grounded signals when present:
        * recommendation decisions — accepted counts full, modified counts half,
          rejected counts zero (following the AI's advice = adherence);
        * plan-task completion — done / total.
        The overall score is the mean of whichever signals exist, 0..100.
        """
        current = self.strategies.get_current(client_id)

        counts = self.recommendations.current_decision_counts(client_id)
        accepted = counts.get("accepted", 0)
        modified = counts.get("modified", 0)
        rejected = counts.get("rejected", 0)
        total_recs = accepted + modified + rejected
        decision_adherence = (
            round((accepted + 0.5 * modified) / total_recs, 4) if total_recs else None
        )

        tasks_done, tasks_total = self.tasks.completion_counts(client_id)
        task_completion = round(tasks_done / tasks_total, 4) if tasks_total else None

        basis: list[str] = []
        components: list[float] = []
        if decision_adherence is not None:
            basis.append("recommendation_decisions")
            components.append(decision_adherence)
        if task_completion is not None:
            basis.append("task_completion")
            components.append(task_completion)
        adherence_score = round(sum(components) / len(components) * 100) if components else None

        return AdherenceSummary(
            client_id=client_id,
            has_strategy=current is not None,
            current_version=current.version if current is not None else None,
            total_recommendations=total_recs,
            accepted=accepted,
            modified=modified,
            rejected=rejected,
            decision_adherence=decision_adherence,
            tasks_total=tasks_total,
            tasks_done=tasks_done,
            task_completion=task_completion,
            adherence_score=adherence_score,
            basis=basis,
        )
