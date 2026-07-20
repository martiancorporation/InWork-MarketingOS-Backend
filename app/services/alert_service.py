"""KPI-alert use-cases: list, acknowledge/resolve, and the watchdog evaluation.

``evaluate`` scans the client's campaigns and compares each actual metric to its
agreed KPI target, raising (or updating, or auto-resolving) alerts. It is
deterministic and grounded in the stored numbers — never fabricated — and
dedups by ``rec_key`` so re-running updates the same alert instead of piling up
duplicates. This is the backend for the daily-digest / notification watchdog
(KPI thresholds → operator alert with the reason).

Client-access scoping is enforced at the router. Repos flush; service commits.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.pagination import PaginationParams
from app.models.alert import Alert
from app.models.campaign import Campaign
from app.models.enums import AlertKind, AlertSeverity, AlertStatus
from app.repositories.alert_repository import AlertRepository
from app.repositories.campaign_repository import CampaignRepository
from app.schemas.alert import AlertEvaluateResult, AlertListResponse, AlertRead


class AlertService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.alerts = AlertRepository(db)
        self.campaigns = CampaignRepository(db)

    # ---- reads --------------------------------------------------------- #

    def list_alerts(
        self,
        client_id: uuid.UUID,
        *,
        pagination: PaginationParams,
        status: str | None = None,
        severity: str | None = None,
        kind: str | None = None,
    ) -> AlertListResponse:
        rows, total = self.alerts.list_for_client(
            client_id,
            status=status,
            severity=severity,
            kind=kind,
            offset=pagination.offset,
            limit=pagination.limit,
        )
        return AlertListResponse(
            items=[AlertRead.model_validate(a) for a in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def get_alert(self, client_id: uuid.UUID, alert_id: uuid.UUID) -> Alert:
        alert = self.alerts.get_for_client(client_id, alert_id)
        if alert is None:
            raise NotFoundError("Alert not found.")
        return alert

    # ---- workflow ------------------------------------------------------ #

    def acknowledge(
        self, client_id: uuid.UUID, alert_id: uuid.UUID, *, actor_id: uuid.UUID
    ) -> Alert:
        alert = self.get_alert(client_id, alert_id)
        alert.status = AlertStatus.acknowledged.value
        alert.acknowledged_by = actor_id
        self.db.commit()
        self.db.refresh(alert)
        return alert

    def resolve(self, client_id: uuid.UUID, alert_id: uuid.UUID, *, actor_id: uuid.UUID) -> Alert:
        alert = self.get_alert(client_id, alert_id)
        alert.status = AlertStatus.resolved.value
        alert.resolved_by = actor_id
        self.db.commit()
        self.db.refresh(alert)
        return alert

    # ---- watchdog evaluation ------------------------------------------ #

    def evaluate(self, client_id: uuid.UUID) -> AlertEvaluateResult:
        campaigns, _ = self.campaigns.list_for_client(client_id, limit=None)
        live = {a.rec_key: a for a in self.alerts.live_for_client(client_id) if a.rec_key}

        findings = [f for c in campaigns for f in _findings_for(c)]
        seen_keys = {f["rec_key"] for f in findings}

        opened = updated = auto_resolved = 0
        for f in findings:
            existing = live.get(f["rec_key"])
            if existing is not None:
                existing.severity = f["severity"]
                existing.title = f["title"]
                existing.detail = f["detail"]
                existing.metric = f["metric"]
                existing.threshold = f["threshold"]
                existing.actual = f["actual"]
                existing.kind = f["kind"]
                updated += 1
            else:
                self.alerts.add(
                    Alert(
                        client_id=client_id,
                        campaign_id=f["campaign_id"],
                        kind=f["kind"],
                        severity=f["severity"],
                        status=AlertStatus.open.value,
                        title=f["title"],
                        detail=f["detail"],
                        metric=f["metric"],
                        threshold=f["threshold"],
                        actual=f["actual"],
                        rec_key=f["rec_key"],
                    )
                )
                opened += 1

        # Auto-resolve live alerts whose breach no longer holds.
        for key, alert in live.items():
            if key not in seen_keys:
                alert.status = AlertStatus.resolved.value
                auto_resolved += 1

        self.db.commit()
        open_rows, _ = self.alerts.list_for_client(
            client_id, status=AlertStatus.open.value, limit=None
        )
        return AlertEvaluateResult(
            evaluated_campaigns=len(campaigns),
            opened=opened,
            updated=updated,
            auto_resolved=auto_resolved,
            alerts=[AlertRead.model_validate(a) for a in open_rows],
        )


def _severity_for(overshoot: float) -> str:
    """Map a fractional breach (0.2 = 20% off target) to a severity band."""
    if overshoot >= 0.5:
        return AlertSeverity.high.value
    if overshoot >= 0.2:
        return AlertSeverity.medium.value
    return AlertSeverity.low.value


def _findings_for(c: Campaign) -> list[dict]:
    """Deterministic KPI checks for one campaign → alert descriptors."""
    out: list[dict] = []
    cpl = (float(c.spend) / c.leads) if c.leads else None
    ctr = (c.clicks / c.impressions * 100) if c.impressions else None
    roas = (float(c.revenue) / float(c.spend)) if c.spend else None

    # CPL over target (lower is better).
    if c.target_cpl and cpl is not None and cpl > float(c.target_cpl):
        overshoot = (cpl - float(c.target_cpl)) / float(c.target_cpl)
        out.append(
            {
                "campaign_id": c.id,
                "kind": AlertKind.alert.value,
                "severity": _severity_for(overshoot),
                "title": f"{c.name}: CPL above target",
                "detail": (
                    f"Cost per lead is ${cpl:.2f} vs the ${float(c.target_cpl):.2f} "
                    f"target ({overshoot * 100:.0f}% over). Review targeting or creative."
                ),
                "metric": "cpl",
                "threshold": float(c.target_cpl),
                "actual": round(cpl, 2),
                "rec_key": f"cpl:{c.id}",
            }
        )

    # CTR under target (higher is better).
    if c.target_ctr and ctr is not None and ctr < float(c.target_ctr):
        shortfall = (float(c.target_ctr) - ctr) / float(c.target_ctr)
        out.append(
            {
                "campaign_id": c.id,
                "kind": AlertKind.alert.value,
                "severity": _severity_for(shortfall),
                "title": f"{c.name}: CTR below target",
                "detail": (
                    f"Click-through rate is {ctr:.2f}% vs the {float(c.target_ctr):.2f}% "
                    f"target. Consider refreshing the creative or headline."
                ),
                "metric": "ctr",
                "threshold": float(c.target_ctr),
                "actual": round(ctr, 2),
                "rec_key": f"ctr:{c.id}",
            }
        )

    # Budget pace (approaching / over budget).
    if c.budget_usd and float(c.spend) >= float(c.budget_usd) * 0.9:
        over = float(c.spend) >= float(c.budget_usd)
        out.append(
            {
                "campaign_id": c.id,
                "kind": AlertKind.alert.value,
                "severity": (AlertSeverity.high if over else AlertSeverity.medium).value,
                "title": f"{c.name}: budget {'exceeded' if over else 'nearly spent'}",
                "detail": (f"Spend ${float(c.spend):.2f} of ${float(c.budget_usd):.2f} budget."),
                "metric": "budget",
                "threshold": float(c.budget_usd),
                "actual": round(float(c.spend), 2),
                "rec_key": f"budget:{c.id}",
            }
        )

    # Opportunity: strong ROAS → consider scaling.
    if roas is not None and roas >= 3:
        out.append(
            {
                "campaign_id": c.id,
                "kind": AlertKind.opportunity.value,
                "severity": AlertSeverity.medium.value,
                "title": f"{c.name}: strong ROAS — consider scaling",
                "detail": (
                    f"Return on ad spend is {roas:.1f}x. Increasing budget here may "
                    f"capture more of the same return."
                ),
                "metric": "roas",
                "threshold": 3.0,
                "actual": round(roas, 2),
                "rec_key": f"roas:{c.id}",
            }
        )
    return out
