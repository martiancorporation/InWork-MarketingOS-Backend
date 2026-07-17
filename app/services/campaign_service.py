"""Campaign use-cases: CRUD, A/B comparison, and a target-relative health score.

Client-access scoping is enforced at the router (``ClientService.get_client``)
before any method here runs. Repositories flush; this service owns the commit.

The health score is deterministic and grounded: it measures actuals against the
agreed KPI targets, never fabricated. It's a project-level campaign-health score
(goal-relative, cross-platform), distinct from a single ad platform's own score.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.pagination import PaginationParams
from app.core.request_context import set_audit_changes
from app.models.campaign import Campaign
from app.repositories.campaign_repository import CampaignRepository
from app.schemas.campaign import (
    CampaignCompareResponse,
    CampaignCompareRow,
    CampaignCreate,
    CampaignHealth,
    CampaignListItem,
    CampaignListResponse,
    CampaignUpdate,
    HealthDriver,
)
from app.services.audit_service import created_changes, deleted_changes

# Metric direction for A/B winner selection.
_HIGHER_BETTER = ("ctr", "conversion_rate", "roas", "leads")


class CampaignService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.campaigns = CampaignRepository(db)

    # ---- reads --------------------------------------------------------- #

    def list_campaigns(
        self,
        client_id: uuid.UUID,
        *,
        pagination: PaginationParams,
        status: str | None = None,
    ) -> CampaignListResponse:
        rows, total = self.campaigns.list_for_client(
            client_id, status=status, offset=pagination.offset, limit=pagination.limit
        )
        return CampaignListResponse(
            items=[CampaignListItem.model_validate(c) for c in rows],
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
        )

    def get_campaign(self, client_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
        campaign = self.campaigns.get_for_client(client_id, campaign_id)
        if campaign is None:
            raise NotFoundError("Campaign not found.")
        return campaign

    # ---- writes -------------------------------------------------------- #

    def create_campaign(
        self, client_id: uuid.UUID, data: CampaignCreate, *, created_by: uuid.UUID
    ) -> Campaign:
        campaign = Campaign(
            client_id=client_id,
            name=data.name,
            objective=data.objective.value,
            status=data.status.value,
            start_date=data.start_date,
            end_date=data.end_date,
            budget_usd=data.budget_usd,
            notes=data.notes,
            target_cpl=data.target_cpl,
            target_ctr=data.target_ctr,
            target_conversion_rate=data.target_conversion_rate,
            created_by=created_by,
        )
        self.campaigns.add(campaign)
        self.db.flush()
        set_audit_changes(
            created_changes(
                {"name": campaign.name, "status": campaign.status, "budget_usd": campaign.budget_usd}
            )
        )
        self.db.commit()
        self.db.refresh(campaign)
        return campaign

    def update_campaign(
        self, client_id: uuid.UUID, campaign_id: uuid.UUID, data: CampaignUpdate
    ) -> Campaign:
        campaign = self.get_campaign(client_id, campaign_id)
        fields = data.model_fields_set
        # enum fields → store their string value
        if "objective" in fields and data.objective is not None:
            campaign.objective = data.objective.value
        if "status" in fields and data.status is not None:
            campaign.status = data.status.value
        for attr in (
            "name",
            "start_date",
            "end_date",
            "budget_usd",
            "notes",
            "target_cpl",
            "target_ctr",
            "target_conversion_rate",
            "impressions",
            "clicks",
            "conversions",
            "leads",
            "spend",
            "revenue",
        ):
            if attr in fields:
                setattr(campaign, attr, getattr(data, attr))
        self.db.commit()
        self.db.refresh(campaign)
        return campaign

    def delete_campaign(self, client_id: uuid.UUID, campaign_id: uuid.UUID) -> None:
        campaign = self.get_campaign(client_id, campaign_id)
        set_audit_changes(
            deleted_changes(
                {"name": campaign.name, "status": campaign.status, "budget_usd": campaign.budget_usd}
            )
        )
        self.db.delete(campaign)
        self.db.commit()

    # ---- A/B comparison ------------------------------------------------ #

    def compare(
        self, client_id: uuid.UUID, ids: list[uuid.UUID]
    ) -> CampaignCompareResponse:
        rows = self.campaigns.get_many_for_client(client_id, ids)
        if not rows:
            raise NotFoundError("No matching campaigns to compare.")
        compare_rows = [CampaignCompareRow.model_validate(c) for c in rows]
        winners: dict[str, uuid.UUID] = {}
        for metric in (*_HIGHER_BETTER, "cpl"):
            best_id: uuid.UUID | None = None
            best_val: float | None = None
            higher_better = metric in _HIGHER_BETTER
            for row in compare_rows:
                val = getattr(row, metric)
                if val is None:
                    continue
                if (
                    best_val is None
                    or (higher_better and val > best_val)
                    or (not higher_better and val < best_val)
                ):
                    best_val, best_id = val, row.id
            if best_id is not None:
                winners[metric] = best_id
        return CampaignCompareResponse(rows=compare_rows, winners=winners)

    # ---- health (target-relative) ------------------------------------- #

    def health(self, client_id: uuid.UUID, campaign_id: uuid.UUID) -> CampaignHealth:
        c = self.get_campaign(client_id, campaign_id)
        return self._compute_health(c)

    @staticmethod
    def _compute_health(c: Campaign) -> CampaignHealth:
        subs: list[float] = []
        drivers: list[HealthDriver] = []

        # Actual derived metrics (guarded for div-by-zero).
        cpl = (float(c.spend) / c.leads) if c.leads else None
        ctr = (c.clicks / c.impressions * 100) if c.impressions else None
        cvr = (c.conversions / c.clicks * 100) if c.clicks else None

        if c.target_cpl and cpl is not None:  # lower is better
            sub = min(100.0, float(c.target_cpl) / cpl * 100) if cpl else 100.0
            subs.append(sub)
            drivers.append(
                HealthDriver(label="CPL vs target", delta=round(float(c.target_cpl) - cpl, 2))
            )
        if c.target_ctr and ctr is not None:  # higher is better
            subs.append(min(100.0, ctr / float(c.target_ctr) * 100))
            drivers.append(
                HealthDriver(label="CTR vs target", delta=round(ctr - float(c.target_ctr), 2))
            )
        if c.target_conversion_rate and cvr is not None:  # higher is better
            subs.append(min(100.0, cvr / float(c.target_conversion_rate) * 100))
            drivers.append(
                HealthDriver(
                    label="Conv. rate vs target",
                    delta=round(cvr - float(c.target_conversion_rate), 2),
                )
            )

        if not subs:
            return CampaignHealth(
                campaign_id=c.id,
                score=50,
                band="attention",
                drivers=[],
                summary=(
                    "No comparable KPI targets and actuals yet. Set targets on the "
                    "campaign and ingest metrics to enable a health score."
                ),
                has_targets=any(
                    v is not None
                    for v in (c.target_cpl, c.target_ctr, c.target_conversion_rate)
                ),
            )

        score = int(round(sum(subs) / len(subs)))
        band = (
            "excellent" if score >= 85
            else "good" if score >= 70
            else "attention" if score >= 55
            else "critical"
        )
        met = sum(1 for d in drivers if d.delta >= 0)
        summary = (
            f"{c.name}: {met}/{len(drivers)} KPI targets met "
            f"(score {score}/100, {band})."
        )
        return CampaignHealth(
            campaign_id=c.id,
            score=score,
            band=band,
            drivers=drivers,
            summary=summary,
            has_targets=True,
        )
