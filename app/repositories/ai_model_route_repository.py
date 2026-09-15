"""Query-only data access for ``ai_model_routes``. Never commits — the owning
service (``AiModelRouteService``) does."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_model_route import AiModelRoute


class AiModelRouteRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_all(self) -> list[AiModelRoute]:
        return list(
            self.db.scalars(select(AiModelRoute).order_by(AiModelRoute.task_category)).all()
        )

    def get_by_category(self, task_category: str) -> AiModelRoute | None:
        return self.db.scalars(
            select(AiModelRoute).where(AiModelRoute.task_category == task_category)
        ).first()

    def add(self, route: AiModelRoute) -> None:
        self.db.add(route)
        self.db.flush()
