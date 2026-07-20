"""Alerts API (v1) — KPI watchdog signals + acknowledge/resolve workflow.

- ``GET  /clients/{id}/alerts``                    — list (status/severity/kind)
- ``POST /clients/{id}/alerts/evaluate``           — run the KPI watchdog pass
- ``GET  /clients/{id}/alerts/{alert_id}``         — detail
- ``POST /clients/{id}/alerts/{alert_id}/acknowledge`` — an operator owns it
- ``POST /clients/{id}/alerts/{alert_id}/resolve``     — mark handled

Client-access-scoped via ``ClientService.get_client``. ``/evaluate`` is declared
before ``/{alert_id}`` so the literal segment wins the match.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, DbSession, Pagination, require_capability
from app.models.client import Client
from app.models.enums import (
    AlertKind,
    AlertSeverity,
    AlertStatus,
    ClientCapability,
)
from app.schemas.alert import AlertEvaluateResult, AlertListResponse, AlertRead
from app.services.alert_service import AlertService
from app.services.client_service import ClientService

router = APIRouter(prefix="/clients/{client_id}/alerts", tags=["alerts"])


@router.get("", response_model=AlertListResponse, summary="List KPI alerts")
def list_alerts(
    client_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    pagination: Pagination,
    status_filter: AlertStatus | None = Query(None, alias="status"),
    severity: AlertSeverity | None = Query(None),
    kind: AlertKind | None = Query(None),
) -> AlertListResponse:
    ClientService(db).get_client(user, client_id)
    return AlertService(db).list_alerts(
        client_id,
        pagination=pagination,
        status=status_filter.value if status_filter else None,
        severity=severity.value if severity else None,
        kind=kind.value if kind else None,
    )


@router.post(
    "/evaluate",
    response_model=AlertEvaluateResult,
    summary="Run the KPI watchdog over the client's campaigns",
)
def evaluate_alerts(client_id: uuid.UUID, user: CurrentUser, db: DbSession) -> AlertEvaluateResult:
    ClientService(db).get_client(user, client_id)
    return AlertService(db).evaluate(client_id)


@router.get("/{alert_id}", response_model=AlertRead, summary="Get an alert")
def get_alert(
    client_id: uuid.UUID, alert_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> AlertRead:
    ClientService(db).get_client(user, client_id)
    return AlertRead.model_validate(AlertService(db).get_alert(client_id, alert_id))


@router.post("/{alert_id}/acknowledge", response_model=AlertRead, summary="Acknowledge an alert")
def acknowledge_alert(
    client_id: uuid.UUID,
    alert_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    # Acknowledging is a "review results" responsibility (BE-03).
    _client: Annotated[Client, Depends(require_capability(ClientCapability.review_results))],
) -> AlertRead:
    return AlertRead.model_validate(
        AlertService(db).acknowledge(client_id, alert_id, actor_id=user.id)
    )


@router.post("/{alert_id}/resolve", response_model=AlertRead, summary="Resolve an alert")
def resolve_alert(
    client_id: uuid.UUID, alert_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> AlertRead:
    ClientService(db).get_client(user, client_id)
    return AlertRead.model_validate(AlertService(db).resolve(client_id, alert_id, actor_id=user.id))
