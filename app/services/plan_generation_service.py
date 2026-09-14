"""AI content-calendar generation use-cases: propose a month of draft items,
then assign / reject / regenerate one at a time.

Client-access scoping is enforced at the router. A proposed item is persisted
immediately as a ``MarketingEvent`` (draft, pending approval) + a linked
``PlanTask`` (todo, unassigned) in one commit — nothing lives only in frontend
memory, so a refresh never loses a generated plan. Repositories flush; this
service owns the commit.
"""

from __future__ import annotations

import calendar as _calendar
import uuid
from datetime import date, time

from sqlalchemy.orm import Session

from app.ai.features import AiFeature
from app.ai.plan_generation import PlanGenerationAgent
from app.ai.usage import AiUsageContext
from app.core.exceptions import BadRequestError, NotFoundError
from app.integrations.embeddings import get_embedder
from app.integrations.llm import get_llm_client
from app.models.enums import (
    ApprovalStatus,
    EventStage,
    EventType,
    SocialPlatform,
    TaskCategory,
    TaskStatus,
)
from app.models.event import EventActivity, EventPost, MarketingEvent
from app.models.plan import PlanTask
from app.models.user import User
from app.repositories.plan_repository import PlanTaskRepository
from app.schemas.plan import PlanTaskRead
from app.schemas.plan_generation import (
    GeneratedPlanTaskRead,
    PlanGenerationAssign,
    PlanGenerationRegenerate,
    PlanGenerationReject,
    PlanTaskContentRead,
)
from app.services.notification_service import NotificationService


class PlanGenerationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tasks = PlanTaskRepository(db)

    async def propose_month(
        self, client_id: uuid.UUID, prompt: str, month: str, *, user: User
    ) -> list[GeneratedPlanTaskRead]:
        year, mon = (int(p) for p in month.split("-"))
        existing_titles = self._existing_titles(client_id, year, mon)

        agent = self._agent(client_id, user)
        proposed_items = await agent.generate_month(
            prompt, year=year, month=mon, existing_titles=existing_titles
        )

        created: list[GeneratedPlanTaskRead] = []
        for proposed in proposed_items:
            event = MarketingEvent(
                client_id=client_id,
                title=proposed.title,
                type=EventType.content,
                platform=SocialPlatform(proposed.platform),
                event_date=proposed.event_date,
                event_time=time(9, 0),
                stage=EventStage.draft,
                approval_status=ApprovalStatus.pending,
                created_by=user.id,
                post=EventPost(
                    caption=proposed.caption,
                    hashtags=proposed.hashtags,
                    content_format=proposed.content_format,
                ),
            )
            self.db.add(event)
            self.db.flush()  # assign event.id before the task references it

            task = PlanTask(
                client_id=client_id,
                title=proposed.title,
                description=proposed.caption,
                category=TaskCategory(proposed.category),
                status=TaskStatus.todo,
                event_id=event.id,
                start_date=proposed.event_date,
                due_date=proposed.event_date,
                created_by=user.id,
            )
            self.tasks.add(task)
            self.tasks.flush()
            created.append(_read(task, event))

        self.db.commit()
        return created

    def assign(
        self,
        client_id: uuid.UUID,
        task_id: uuid.UUID,
        data: PlanGenerationAssign,
        *,
        actor: User,
    ) -> GeneratedPlanTaskRead:
        """Manager review step: approve the generated content and hand the task
        to a team member in one action (per the meeting: assignment *is* the
        manager's approval of the plan)."""
        task = self._require_task(client_id, task_id)
        task.assignee_id = data.assignee_id
        if data.priority is not None:
            task.priority = data.priority
        if data.due_date is not None:
            task.due_date = data.due_date
            task.start_date = task.start_date or data.due_date
        task.status = TaskStatus.in_progress

        event = task.event
        if event is not None:
            event.approval_status = ApprovalStatus.approved
            event.approved_by = actor.id
            event.stage = EventStage.scheduled
            event.activity.append(
                EventActivity(action="status_change", note="approved via assignment", user_id=actor.id)
            )
        self.db.commit()

        NotificationService(self.db).notify(
            data.assignee_id,
            title=f"New task assigned: {task.title}",
            client_id=client_id,
            link=f"/clients/{client_id}/plan",
            rec_key=f"task_assigned:{task.id}",
        )
        return _read(task, event)

    def reject(
        self, client_id: uuid.UUID, task_id: uuid.UUID, data: PlanGenerationReject, *, actor: User
    ) -> GeneratedPlanTaskRead:
        """Cancel a generated item with a mandatory reason. Admin-only, enforced
        at the router — per the meeting, only the admin operator can cancel a
        generated plan item, and must record why."""
        task = self._require_task(client_id, task_id)
        task.status = TaskStatus.blocked
        event = task.event
        if event is not None:
            event.approval_status = ApprovalStatus.rejected
            event.approval_note = data.reason
            event.activity.append(
                EventActivity(
                    action="status_change", note=f"rejected: {data.reason}", user_id=actor.id
                )
            )
        self.db.commit()
        return _read(task, event)

    async def regenerate_item(
        self,
        client_id: uuid.UUID,
        task_id: uuid.UUID,
        data: PlanGenerationRegenerate,
        *,
        actor: User,
    ) -> GeneratedPlanTaskRead:
        """Real-time single-day edit: re-run generation for exactly this one
        item, per a client change request, leaving every other day untouched.
        Admin-only, enforced at the router."""
        task = self._require_task(client_id, task_id)
        event = task.event
        if event is None:
            raise BadRequestError("This task has no linked calendar item to regenerate.")

        agent = self._agent(client_id, actor)
        proposed = await agent.regenerate_item(
            current_title=task.title,
            current_caption=event.post.caption if event.post else None,
            event_date=event.event_date,
            instructions=data.instructions,
            reason=data.reason,
        )

        task.title = proposed.title
        task.description = proposed.caption
        task.category = TaskCategory(proposed.category)
        # Back to draft — the new content goes through review again, same as
        # any fresh proposal, rather than silently staying "approved".
        task.status = TaskStatus.todo

        event.title = proposed.title
        event.platform = SocialPlatform(proposed.platform)
        event.stage = EventStage.draft
        event.approval_status = ApprovalStatus.pending
        event.approval_note = None
        event.approved_by = None
        if event.post is None:
            event.post = EventPost(
                caption=proposed.caption,
                hashtags=proposed.hashtags,
                content_format=proposed.content_format,
            )
        else:
            event.post.caption = proposed.caption
            event.post.hashtags = proposed.hashtags
            event.post.content_format = proposed.content_format
        event.activity.append(
            EventActivity(action="edit", note=f"regenerated: {data.reason}", user_id=actor.id)
        )

        self.db.commit()
        return _read(task, event)

    # ---- helpers --------------------------------------------------------- #

    def _agent(self, client_id: uuid.UUID, user: User) -> PlanGenerationAgent:
        return PlanGenerationAgent(
            self.db,
            client_id,
            embedder=get_embedder(),
            ai_client=get_llm_client(
                AiUsageContext(feature=AiFeature.PLAN_GENERATION, client_id=client_id, user_id=user.id)
            ),
        )

    def _require_task(self, client_id: uuid.UUID, task_id: uuid.UUID) -> PlanTask:
        task = self.tasks.get_for_client(client_id, task_id)
        if task is None:
            raise NotFoundError("Task not found.")
        return task

    def _existing_titles(self, client_id: uuid.UUID, year: int, month: int) -> list[str]:
        last_day = _calendar.monthrange(year, month)[1]
        rows, _ = self.tasks.list_for_client(
            client_id,
            start=date(year, month, 1),
            end=date(year, month, last_day),
            include_undated=False,
            offset=0,
            limit=200,
        )
        return [r.title for r in rows]


def _read(task: PlanTask, event: MarketingEvent | None) -> GeneratedPlanTaskRead:
    base = PlanTaskRead.model_validate(task)
    content = None
    if event is not None:
        content = PlanTaskContentRead(
            event_id=event.id,
            platform=event.platform.value,
            event_date=event.event_date,
            approval_status=event.approval_status.value,
            stage=event.stage.value,
            caption=event.post.caption if event.post else None,
            hashtags=event.post.hashtags if event.post else None,
            content_format=event.post.content_format if event.post else None,
        )
    return GeneratedPlanTaskRead(**base.model_dump(), content=content)
