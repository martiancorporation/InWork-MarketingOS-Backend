"""Admin AI model-routing use-cases.

Backs ``GET/PUT /admin/ai-model-routes`` — the actual "update the routing
without a redeploy" mechanism: an admin changes which model handles a task
category here, and every AI call in that category (through
``app.ai.model_router.model_for``) picks it up within one cache TTL (30s), or
immediately in the worker that made the change (writes invalidate the cache).

The model catalog (what an admin may pick, and what a category may already be
seeded with) always comes from OpenRouter's live model list
(``app/integrations/llm/model_catalog.py``) — there is no static list of
"known" models maintained in this codebase.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ai.model_router import ALL_CATEGORIES, bootstrap_model_id, invalidate_cache
from app.core.exceptions import BadRequestError, NotFoundError
from app.integrations.llm import model_catalog
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
        """Every known task category, one row each — lazily creating a row
        for any category that doesn't have one yet (same self-healing GET
        pattern as NotificationService.get_preferences), so a new category
        added by a future code change just shows up, the next time this list
        is loaded, seeded from the live catalog's cheapest model — a safe,
        non-hardcoded starting point, not a quality recommendation. If the
        catalog can't be reached at seed time, the row is created inactive
        with no model rather than guessing a literal id."""
        existing = {r.task_category: r for r in self.repo.list_all()}
        created = False
        for category in ALL_CATEGORIES:
            if category not in existing:
                model_id = bootstrap_model_id()
                route = AiModelRoute(
                    task_category=category,
                    model_id=model_id,
                    is_active=model_id is not None,
                    notes=(
                        "Bootstrapped from the cheapest available model — not yet tuned by an admin."
                        if model_id
                        else "Not configured yet — the model catalog was unreachable when this "
                        "category was first seeded. Pick a model below."
                    ),
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
        if model_catalog.get_model(data.model_id) is None:
            raise BadRequestError(
                f"'{data.model_id}' is not in OpenRouter's current model catalog. "
                "See GET /admin/ai-model-routes/available-models."
            )
        if data.fallback_model_id and model_catalog.get_model(data.fallback_model_id) is None:
            raise BadRequestError(
                f"'{data.fallback_model_id}' is not in OpenRouter's current model catalog."
            )

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
        """Every currently-selectable OpenRouter model with a fixed per-token
        rate — excludes the catalog's variable/auto-routing meta-models
        (e.g. an "auto" router that picks its own underlying model per
        request), since those have no fixed price to show and aren't a real
        pinned model choice for a task category."""
        return [
            AvailableModel(
                model_id=m.id,
                label=m.name,
                input_per_1m=float(m.input_per_million),
                output_per_1m=float(m.output_per_million)
                if m.output_per_million is not None
                else 0.0,
            )
            for m in model_catalog.get_catalog()
            if m.input_per_million is not None
        ]
