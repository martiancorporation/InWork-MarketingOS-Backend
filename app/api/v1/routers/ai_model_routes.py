"""Admin AI model-routing API — the dynamic replacement for the old static
cheap/mid/expensive model config.

- ``GET /admin/ai-model-routes`` — every task category + its current model.
- ``PUT /admin/ai-model-routes/{task_category}`` — change a category's model.
- ``GET /admin/ai-model-routes/available-models`` — the model picker's catalog.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AdminUser, DbSession
from app.schemas.ai_model_route import (
    AiModelRouteListResponse,
    AiModelRouteRead,
    AiModelRouteUpdate,
    AvailableModelsResponse,
)
from app.services.ai_model_route_service import AiModelRouteService

router = APIRouter(prefix="/admin/ai-model-routes", tags=["ai-model-routes"])


@router.get("", response_model=AiModelRouteListResponse, summary="List AI model routes (admin)")
def list_routes(admin: AdminUser, db: DbSession) -> AiModelRouteListResponse:
    routes = AiModelRouteService(db).list_routes()
    return AiModelRouteListResponse(items=[AiModelRouteRead.model_validate(r) for r in routes])


@router.get(
    "/available-models",
    response_model=AvailableModelsResponse,
    summary="Known-model catalog for the routing picker (admin)",
)
def available_models(admin: AdminUser, db: DbSession) -> AvailableModelsResponse:
    return AvailableModelsResponse(items=AiModelRouteService(db).available_models())


@router.put(
    "/{task_category}",
    response_model=AiModelRouteRead,
    summary="Change which model a task category routes to (admin)",
)
def update_route(
    task_category: str, data: AiModelRouteUpdate, admin: AdminUser, db: DbSession
) -> AiModelRouteRead:
    route = AiModelRouteService(db).update_route(task_category, data, updated_by=admin.id)
    return AiModelRouteRead.model_validate(route)
