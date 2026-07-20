"""Strategy-adherence API (v1) — BE-06.

- ``PUT  /clients/{id}/strategy``            — record/replace the current strategy
- ``GET  /clients/{id}/strategy``            — read the current strategy
- ``GET  /clients/{id}/strategy/adherence``  — adherence summary (deterministic)

Every route is client-access-scoped via ``ClientService.get_client`` (admin or
assigned user); an inaccessible client returns 404. ``/adherence`` is declared
before the bare ``/strategy`` GET so the literal segment wins the match.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.strategy import AdherenceSummary, StrategyCreate, StrategyRead
from app.services.client_service import ClientService
from app.services.strategy_service import StrategyService

router = APIRouter(prefix="/clients/{client_id}/strategy", tags=["strategy"])


@router.put(
    "",
    response_model=StrategyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record the current strategy the operator signs off on",
)
def set_strategy(
    client_id: uuid.UUID, data: StrategyCreate, user: CurrentUser, db: DbSession
) -> StrategyRead:
    ClientService(db).get_client(user, client_id)  # 404 if inaccessible
    strategy = StrategyService(db).set_strategy(client_id, data, signed_by=user.id)
    return StrategyRead.model_validate(strategy)


@router.get(
    "/adherence",
    response_model=AdherenceSummary,
    summary="How closely the operator followed the recorded strategy",
)
def get_adherence(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> AdherenceSummary:
    ClientService(db).get_client(user, client_id)
    return StrategyService(db).adherence(client_id)


@router.get("", response_model=StrategyRead, summary="Get the current strategy")
def get_strategy(
    client_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> StrategyRead:
    ClientService(db).get_client(user, client_id)
    return StrategyRead.model_validate(StrategyService(db).get_current(client_id))
