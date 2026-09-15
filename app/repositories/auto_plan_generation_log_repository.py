"""Query-only data access for the automatic month-ahead plan generation
dedupe log. The owning service (``SchedulerService``) commits."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auto_plan_generation_log import AutoPlanGenerationLog


class AutoPlanGenerationLogRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def exists(self, client_id: uuid.UUID, period: str) -> bool:
        return (
            self.db.scalar(
                select(AutoPlanGenerationLog.id).where(
                    AutoPlanGenerationLog.client_id == client_id,
                    AutoPlanGenerationLog.period == period,
                )
            )
            is not None
        )

    def add(self, log: AutoPlanGenerationLog) -> None:
        self.db.add(log)
        self.db.flush()
