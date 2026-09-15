"""Admin AI model-routing use-cases.

Backs ``GET/PUT /admin/ai-model-routes`` — the actual "update the routing
without a redeploy" mechanism: an admin changes which model handles a task
category here, and every AI call in that category (through
``app.ai.model_router.model_for``) picks it up within one cache TTL (30s), or
immediately in the worker that made the change (writes invalidate the cache).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ai.model_router import (
    ALL_CATEGORIES,
    KNOWN_MODEL_IDS,
    KNOWN_MODELS,
    builtin_default,
    invalidate_cache,
)
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.ai_model_route import AiModelRoute
from app.repositories.ai_model_route_repository import AiModelRouteRepository
from app.schemas.ai_model_route import (
    AiModelRouteUpdate,
    AvailableModel,
)


class AiModelRouteService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = AiModelRouteRepository(db)

    def list_routes(self) -> list[AiModelRoute]:
        """Every known task category, one row each — lazily creating a
        built-in-default row for any category that doesn't have one yet
        (same self-healing GET pattern as NotificationService.get_preferences),
        so a new category added by a future code change just shows up with a
        sane default the next time this list is loaded."""
        existing = {r.task_category: r for r in self.repo.list_all()}
        created = False
        for category in ALL_CATEGORIES:
            if category not in existing:
                route = AiModelRoute(
                    task_category=category,
                    model_id=builtin_default(category) or "",
                    is_active=True,
                    notes="Built-in default — not yet tuned by an admin.",
                )
                self.repo.add(route)
                existing[category] = route
                created = True
        if created:
            self.db.commit()
        return sorted(existing.values(), key=lambda r: r.task_category)

    def update_route(
        self, task_category: str, data: AiModelRouteUpdate, *, updated_by: uuid.UUID
    ) -> AiModelRoute:
        if task_category not in ALL_CATEGORIES:
            raise NotFoundError(f"Unknown task category '{task_category}'.")
        if data.model_id not in KNOWN_MODEL_IDS:
            raise BadRequestError(
                f"'{data.model_id}' is not in the known model catalog. "
                "See GET /admin/ai-model-routes/available-models."
            )
        if data.fallback_model_id and data.fallback_model_id not in KNOWN_MODEL_IDS:
            raise BadRequestError(f"'{data.fallback_model_id}' is not in the known model catalog.")

        route = self.repo.get_by_category(task_category)
        is_new = route is None
        if route is None:
            route = AiModelRoute(task_category=task_category)

        route.model_id = data.model_id
        route.fallback_model_id = data.fallback_model_id
        route.is_active = data.is_active
        route.notes = data.notes
        route.updated_by = updated_by
        if is_new:
            self.repo.add(route)
        self.db.commit()
        self.db.refresh(route)
        invalidate_cache()
        return route

    def available_models(self) -> list[AvailableModel]:
        return [
            AvailableModel(
                model_id=str(m["model_id"]),
                label=str(m["label"]),
                input_per_1m=float(m["input"]),
                output_per_1m=float(m["output"]),
            )
            for m in KNOWN_MODELS
        ]
