"""The AI change-proposal engine: dry-run staging and governed execution.

Every mutation the AI command layer can make goes through here, never through
a service's public (committing) method directly. A ``stage_*`` method
validates a proposed operation by calling the REAL non-committing ``_apply_*``
half of the relevant service inside a SAVEPOINT that is always rolled back
(see ``PlanService``/``AssignmentService``) — so the exact same business rules
and diffs run at propose time as at execute time, with no duplicated logic and
zero risk of the "dry run" silently writing for real.

Nothing reaches the database until ``approve`` is called. ``approve`` runs in
three separate transactions so a mid-execution failure can still be recorded
after the rollback that undoes it:

  1. **Claim** — compare-and-swap ``pending_approval -> executing`` (its own
     commit). A losing concurrent/duplicate call just reads back the result.
  2. **Validate + execute** — re-check expiry, capability/role, and whole-row
     staleness; then replay every ``_apply_*`` in ``seq`` order in one
     transaction, writing one manual ``AuditLog`` row per operation. One final
     commit persists every mutated entity + every audit row + the proposal's
     ``completed`` status together.
  3. **Record failure** (only on a mid-loop exception) — the transaction-2
     rollback undoes every mutation and the audit rows, but also would drop
     the failure reason, so it's written in a fresh transaction 3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.models.audit import AuditLog
from app.models.enums import (
    ClientCapability,
    ProposalStatus,
    ProposedOperationStatus,
    ProposedOperationType,
    UserRole,
)
from app.models.proposal import ChangeProposal, ProposedOperation
from app.models.user import User
from app.repositories.proposal_repository import ProposalRepository
from app.repositories.user_repository import UserRepository
from app.schemas.client import ClientUpdate
from app.schemas.common import MAX_TEXT
from app.schemas.onboarding import BrandUpdate
from app.schemas.plan import PlanTaskCreate, PlanTaskNoteCreate, PlanTaskUpdate
from app.schemas.user import UserUpdate
from app.services.assignment_service import AssignmentService
from app.services.audit_service import created_changes, deleted_changes, field_changes
from app.services.client_service import ClientService
from app.services.onboarding_service import OnboardingService
from app.services.plan_service import PlanService
from app.services.user_service import UserService

#: How long a proposal stays approvable before it must be re-created.
DEFAULT_TTL_MINUTES = 45


@dataclass
class StagedOperation:
    """One write-tool call, already dry-run validated, not yet persisted."""

    operation_type: ProposedOperationType
    entity_type: str
    entity_id: uuid.UUID | None
    entity_label: str | None
    field_changes: dict | None
    payload: dict
    base_snapshot: dict | None = None
    required_capability: ClientCapability | None = None
    requires_admin: bool = False


@dataclass
class TurnDraft:
    """Accumulates staged operations across one AI chat turn's tool calls."""

    operations: list[StagedOperation] = field(default_factory=list)

    def add(self, op: StagedOperation) -> None:
        self.operations.append(op)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ProposalService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.proposals = ProposalRepository(db)
        self.plans = PlanService(db)
        self.assignments = AssignmentService(db)
        self.users = UserRepository(db)
        self.clients = ClientService(db)
        self.onboarding = OnboardingService(db)
        self.user_service = UserService(db)

    # ---- staging (dry run; never commits) --------------------------------- #

    def stage_plan_task_create(
        self, client_id: uuid.UUID, user: User, data: PlanTaskCreate
    ) -> StagedOperation:
        self.clients.get_client(user, client_id)  # 404 if inaccessible
        nested = self.db.begin_nested()
        try:
            task, changes = self.plans._apply_create_task(client_id, data, created_by=user.id)
            label = task.title
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.create,
            entity_type="plan_task",
            entity_id=None,
            entity_label=label,
            field_changes=changes,
            payload={"data": data.model_dump(mode="json"), "created_by": str(user.id)},
        )

    def stage_plan_task_update(
        self, client_id: uuid.UUID, user: User, task_id: uuid.UUID, data: PlanTaskUpdate
    ) -> StagedOperation:
        self.clients.get_client(user, client_id)
        existing = self.plans.get_task(client_id, task_id)  # 404 if missing/cross-client
        base_snapshot = {"updated_at": existing.updated_at.isoformat()}
        label = existing.title
        nested = self.db.begin_nested()
        try:
            _task, changes = self.plans._apply_update_task(client_id, task_id, data)
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.update,
            entity_type="plan_task",
            entity_id=task_id,
            entity_label=label,
            field_changes=changes,
            payload={"data": data.model_dump(mode="json", exclude_unset=True)},
            base_snapshot=base_snapshot,
        )

    def stage_plan_task_delete(
        self, client_id: uuid.UUID, user: User, task_id: uuid.UUID
    ) -> StagedOperation:
        self.clients.get_client(user, client_id)
        existing = self.plans.get_task(client_id, task_id)
        base_snapshot = {"updated_at": existing.updated_at.isoformat()}
        label = existing.title
        nested = self.db.begin_nested()
        try:
            _task, changes = self.plans._apply_delete_task(client_id, task_id)
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.delete,
            entity_type="plan_task",
            entity_id=task_id,
            entity_label=label,
            field_changes=changes,
            payload={},
            base_snapshot=base_snapshot,
        )

    def stage_plan_task_duplicate(
        self, client_id: uuid.UUID, user: User, task_id: uuid.UUID
    ) -> StagedOperation:
        self.clients.get_client(user, client_id)
        nested = self.db.begin_nested()
        try:
            copy, changes = self.plans._apply_duplicate_task(client_id, task_id, created_by=user.id)
            label = copy.title
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.create,
            entity_type="plan_task",
            entity_id=None,
            entity_label=label,
            field_changes=changes,
            payload={"source_task_id": str(task_id), "created_by": str(user.id)},
        )

    def stage_plan_task_add_note(
        self, client_id: uuid.UUID, user: User, task_id: uuid.UUID, data: PlanTaskNoteCreate
    ) -> StagedOperation:
        self.clients.get_client(user, client_id)
        nested = self.db.begin_nested()
        try:
            note, task = self.plans._apply_add_note(client_id, task_id, data, user_id=user.id)
            label, body = task.title, note.body
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.create,
            entity_type="plan_task_note",
            entity_id=None,
            entity_label=label,
            field_changes=created_changes({"body": body}),
            payload={
                "task_id": str(task_id),
                "data": data.model_dump(mode="json"),
                "user_id": str(user.id),
            },
        )

    def stage_assignment_assign(
        self,
        client_id: uuid.UUID,
        user: User,
        target_user_id: uuid.UUID,
        capabilities: list[ClientCapability] | None = None,
    ) -> StagedOperation:
        self.clients.get_client(user, client_id)
        if user.role != UserRole.admin:
            raise ForbiddenError("Administrator privileges are required to manage assignments.")
        target = self.users.get(target_user_id)
        if target is None:
            raise NotFoundError("User not found.")
        nested = self.db.begin_nested()
        try:
            self.assignments._apply_assign(
                client_id, target_user_id, assigned_by=user.id, capabilities=capabilities
            )
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.assign,
            entity_type="client_assignment",
            entity_id=None,
            entity_label=target.email,
            field_changes=created_changes(
                {
                    "user_email": target.email,
                    "capabilities": [c.value for c in capabilities]
                    if capabilities
                    else "full access",
                }
            ),
            payload={
                "user_id": str(target_user_id),
                "capabilities": [c.value for c in capabilities] if capabilities else None,
                "assigned_by": str(user.id),
            },
            requires_admin=True,
        )

    def stage_assignment_set_capabilities(
        self,
        client_id: uuid.UUID,
        user: User,
        target_user_id: uuid.UUID,
        capabilities: list[ClientCapability],
    ) -> StagedOperation:
        self.clients.get_client(user, client_id)
        if user.role != UserRole.admin:
            raise ForbiddenError("Administrator privileges are required to manage assignments.")
        existing = self.assignments.assignments.get(client_id, target_user_id)
        if existing is None:
            raise NotFoundError("Assignment not found.")
        before_caps = [c.value for c in self.assignments.to_read(existing).capabilities]
        target = self.users.get(target_user_id)
        nested = self.db.begin_nested()
        try:
            self.assignments._apply_set_capabilities(client_id, target_user_id, capabilities)
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.update,
            entity_type="client_assignment",
            entity_id=existing.id,
            entity_label=target.email if target else None,
            field_changes=field_changes(
                {"capabilities": before_caps}, {"capabilities": [c.value for c in capabilities]}
            ),
            payload={
                "user_id": str(target_user_id),
                "capabilities": [c.value for c in capabilities],
            },
            requires_admin=True,
        )

    def stage_assignment_unassign(
        self, client_id: uuid.UUID, user: User, target_user_id: uuid.UUID
    ) -> StagedOperation:
        self.clients.get_client(user, client_id)
        if user.role != UserRole.admin:
            raise ForbiddenError("Administrator privileges are required to manage assignments.")
        existing = self.assignments.assignments.get(client_id, target_user_id)
        if existing is None:
            raise NotFoundError("Assignment not found.")
        target = self.users.get(target_user_id)
        nested = self.db.begin_nested()
        try:
            self.assignments._apply_unassign(client_id, target_user_id)
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.unassign,
            entity_type="client_assignment",
            entity_id=existing.id,
            entity_label=target.email if target else None,
            field_changes=deleted_changes({"user_email": target.email if target else None}),
            payload={"user_id": str(target_user_id)},
            requires_admin=True,
        )

    def stage_update_client(
        self, client_id: uuid.UUID, user: User, data: ClientUpdate
    ) -> StagedOperation:
        """Admin edit of a client's status/basic profile fields — mirrors
        ``PATCH /clients/{id}`` exactly (same admin-only gate)."""
        existing = self.clients.get_client(user, client_id)  # 404 if inaccessible
        if user.role != UserRole.admin:
            raise ForbiddenError("Administrator privileges are required to edit a client.")
        base_snapshot = {"updated_at": existing.updated_at.isoformat()}
        label = existing.name
        nested = self.db.begin_nested()
        try:
            _client, changes = self.clients._apply_update_client(client_id, data)
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.update,
            entity_type="client",
            entity_id=client_id,
            entity_label=label,
            field_changes=changes,
            payload={"data": data.model_dump(mode="json", exclude_unset=True)},
            base_snapshot=base_snapshot,
            requires_admin=True,
        )

    def stage_update_brand(
        self, client_id: uuid.UUID, user: User, data: BrandUpdate
    ) -> StagedOperation:
        """Brand-settings edit — mirrors the brand section of
        ``PATCH /clients/{id}/onboarding`` (same admin-only gate). See
        ``OnboardingService._apply_update_brand`` for the whole-row-staleness
        caveat on colors/fonts."""
        existing = self.clients.get_client(user, client_id)  # 404 if inaccessible
        if user.role != UserRole.admin:
            raise ForbiddenError("Administrator privileges are required to edit brand settings.")
        base_snapshot = {"updated_at": existing.updated_at.isoformat()}
        label = existing.name
        nested = self.db.begin_nested()
        try:
            changes = self.onboarding._apply_update_brand(existing, data)
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.update,
            entity_type="client_brand",
            entity_id=client_id,
            entity_label=label,
            field_changes=changes,
            payload={"data": data.model_dump(mode="json", exclude_unset=True)},
            base_snapshot=base_snapshot,
            requires_admin=True,
        )

    def stage_update_user(
        self, client_id: uuid.UUID, user: User, target_user_id: uuid.UUID, data: UserUpdate
    ) -> StagedOperation:
        """Change an existing user's name/role/active status — mirrors
        ``PATCH /users/{id}`` exactly (global, admin-only; not scoped to
        ``client_id``, which here is only the chat this was proposed from —
        see the module docstring's note on user-management proposals).

        Deliberately no ``propose_create_user`` counterpart: creating an
        account needs a password, and putting one through the LLM tool-call
        path or resting it in ``ProposedOperation.payload`` for up to
        ``DEFAULT_TTL_MINUTES`` before approval is a real secret-handling risk
        this codebase has no invite/reset-link flow to avoid. Account
        creation stays a human-driven action in the Users & Access UI.
        """
        self.clients.get_client(user, client_id)  # 404 if inaccessible (chat-context check)
        if user.role != UserRole.admin:
            raise ForbiddenError("Administrator privileges are required to manage users.")
        target = self.users.get(target_user_id)
        if target is None:
            raise NotFoundError("User not found.")
        base_snapshot = {"updated_at": target.updated_at.isoformat()}
        label = target.email
        nested = self.db.begin_nested()
        try:
            _user, changes = self.user_service._apply_update_user(target_user_id, data)
        finally:
            nested.rollback()
        return StagedOperation(
            operation_type=ProposedOperationType.update,
            entity_type="user",
            entity_id=target_user_id,
            entity_label=label,
            field_changes=changes,
            payload={"data": data.model_dump(mode="json", exclude_unset=True)},
            base_snapshot=base_snapshot,
            requires_admin=True,
        )

    # ---- proposal lifecycle ------------------------------------------------ #

    def create_proposal(
        self,
        client_id: uuid.UUID,
        *,
        chat_id: uuid.UUID | None,
        message_id: uuid.UUID | None,
        created_by: uuid.UUID,
        raw_request: str,
        summary: str,
        operations: list[StagedOperation],
        ttl_minutes: int = DEFAULT_TTL_MINUTES,
    ) -> ChangeProposal:
        if not operations:
            raise BadRequestError("A proposal must contain at least one operation.")
        now = datetime.now(UTC)
        proposal = ChangeProposal(
            client_id=client_id,
            chat_id=chat_id,
            message_id=message_id,
            created_by_user_id=created_by,
            status=ProposalStatus.pending_approval,
            raw_request=raw_request[:MAX_TEXT],
            summary=summary[:MAX_TEXT] if summary else None,
            created_at=now,
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
        self.proposals.add(proposal)
        self.proposals.flush()
        for seq, op in enumerate(operations):
            self.db.add(
                ProposedOperation(
                    proposal_id=proposal.id,
                    seq=seq,
                    operation_type=op.operation_type,
                    entity_type=op.entity_type,
                    entity_id=op.entity_id,
                    entity_label=op.entity_label,
                    field_changes=op.field_changes,
                    payload=op.payload,
                    base_snapshot=op.base_snapshot,
                    required_capability=op.required_capability.value
                    if op.required_capability
                    else None,
                    requires_admin=op.requires_admin,
                )
            )
        self.db.commit()
        self.db.refresh(proposal)
        return proposal

    def get_proposal(self, client_id: uuid.UUID, proposal_id: uuid.UUID) -> ChangeProposal:
        proposal = self.proposals.get_for_client(client_id, proposal_id)
        if proposal is None:
            raise NotFoundError("Proposal not found.")
        return proposal

    def reject(
        self, client_id: uuid.UUID, proposal_id: uuid.UUID, *, user: User, reason: str | None = None
    ) -> ChangeProposal:
        self.clients.get_client(user, client_id)  # 404 if inaccessible
        proposal = self.get_proposal(client_id, proposal_id)
        if proposal.status != ProposalStatus.pending_approval:
            raise ConflictError("This proposal is no longer pending approval.")
        proposal.status = ProposalStatus.cancelled
        if reason:
            proposal.error = reason[:MAX_TEXT]
        self.db.commit()
        self.db.refresh(proposal)
        return proposal

    def approve(
        self, client_id: uuid.UUID, proposal_id: uuid.UUID, *, user: User
    ) -> ChangeProposal:
        """The only path that ever mutates data on behalf of the AI chat.

        Never raises for an *expected* business failure (expired, lost
        capability, stale row, a mid-execution error) — those are recorded on
        the proposal/operations and returned normally so the caller always
        gets a proposal back with an accurate ``status``. Only a genuine
        authorization failure on the approve call itself (inaccessible
        client) raises, matching every other endpoint's 404 contract.
        """
        self.clients.get_client(user, client_id)  # 404 if inaccessible; re-checked live
        proposal = self.get_proposal(client_id, proposal_id)

        # ---- phase 1: claim (its own committed transaction) ---- #
        claimed = self.proposals.claim_for_execution(proposal_id)
        self.db.commit()
        if claimed == 0:
            # Lost the race (already executing/resolved by another call) —
            # read back whatever it now is rather than re-executing.
            self.db.refresh(proposal)
            return proposal
        self.db.refresh(proposal)  # now status == executing

        # ---- phase 2 (+3 on failure): validate + execute ---- #
        self._validate_and_execute(proposal, user=user)
        self.db.refresh(proposal)
        return proposal

    def _validate_and_execute(self, proposal: ChangeProposal, *, user: User) -> None:
        client_id = proposal.client_id
        operations = list(proposal.operations)

        now = datetime.now(UTC)
        if proposal.expires_at is not None and now > _aware(proposal.expires_at):
            self._fail_preflight(proposal, "This proposal has expired.")
            return

        caps = self.clients.effective_capabilities(user, client_id)
        for op in operations:
            if op.required_capability and ClientCapability(op.required_capability) not in caps:
                self._fail_preflight(
                    proposal,
                    f"You no longer have the '{op.required_capability}' capability "
                    "required for this change.",
                )
                return
            if op.requires_admin and user.role != UserRole.admin:
                self._fail_preflight(proposal, "Administrator privileges are required.")
                return

        for op in operations:
            if not self._matches_live_snapshot(client_id, op):
                self._fail_preflight(
                    proposal,
                    "This has changed since the proposal was created. "
                    "Please review the updated values.",
                )
                return

        failed_seq: int | None = None
        failure_message: str | None = None
        for op in operations:
            try:
                entity_id, label = self._execute_operation(op, user=user)
            except AppError as exc:
                failed_seq = op.seq
                failure_message = exc.message
                break
            op.status = ProposedOperationStatus.executed
            if entity_id is not None:
                op.entity_id = entity_id
            if label is not None:
                op.entity_label = label
            self.db.add(
                AuditLog(
                    actor_user_id=user.id,
                    client_id=client_id,
                    proposal_id=proposal.id,
                    entity=op.entity_type,
                    entity_id=op.entity_id,
                    action=f"{op.entity_type}.{op.operation_type.value}.ai_approved",
                    target_label=op.entity_label,
                    changes=op.field_changes,
                )
            )
            self.db.flush()

        if failed_seq is not None:
            self.db.rollback()  # undoes every mutation + audit row from this loop
            proposal = self.get_proposal(client_id, proposal.id)  # reload (status: executing)
            proposal.status = ProposalStatus.failed
            proposal.error = f"Operation {failed_seq} failed: {failure_message}"
            for op in proposal.operations:
                if op.seq == failed_seq:
                    op.status = ProposedOperationStatus.failed
                    op.error = failure_message
                elif op.status == ProposedOperationStatus.pending:
                    op.status = ProposedOperationStatus.skipped
            self.db.commit()
            return

        proposal.status = ProposalStatus.completed
        proposal.approved_by_user_id = user.id
        proposal.approved_at = now
        proposal.executed_at = now
        self.db.commit()

    def _fail_preflight(self, proposal: ChangeProposal, reason: str) -> None:
        proposal.status = ProposalStatus.failed
        proposal.error = reason
        self.db.commit()

    def _matches_live_snapshot(self, client_id: uuid.UUID, op: ProposedOperation) -> bool:
        """Whole-row staleness check. Only entities with a mutable-row
        ``updated_at`` (``plan_task``, ``client``/``client_brand``, ``user``)
        can be checked this way; entities without one (e.g. ``client_assignment``,
        which has no ``updated_at`` column) rely on their ``_apply_*`` call
        itself re-validating current state at execute time (e.g. re-assigning
        an already-assigned user raises ``ConflictError``)."""
        if op.base_snapshot is None or op.entity_id is None:
            return True
        if op.entity_type == "plan_task":
            live = self.plans.tasks.get_for_client(client_id, op.entity_id)
        elif op.entity_type in ("client", "client_brand"):
            live = self.clients.clients.get(op.entity_id)
        elif op.entity_type == "user":
            live = self.users.get(op.entity_id)
        else:
            return True
        if live is None:
            return False
        return live.updated_at.isoformat() == op.base_snapshot.get("updated_at")

    def _execute_operation(
        self, op: ProposedOperation, *, user: User
    ) -> tuple[uuid.UUID | None, str | None]:
        client_id = op.proposal.client_id
        payload = op.payload or {}
        if op.entity_type == "plan_task":
            return self._execute_plan_task(client_id, op, payload)
        if op.entity_type == "plan_task_note":
            note, task = self.plans._apply_add_note(
                client_id,
                uuid.UUID(payload["task_id"]),
                PlanTaskNoteCreate(**payload["data"]),
                user_id=uuid.UUID(payload["user_id"]),
            )
            return note.id, task.title
        if op.entity_type == "client_assignment":
            return self._execute_assignment(client_id, op, payload)
        if op.entity_type == "client":
            client, changes = self.clients._apply_update_client(
                client_id, ClientUpdate(**payload["data"])
            )
            op.field_changes = changes
            return client.id, client.name
        if op.entity_type == "client_brand":
            client = self.clients.clients.get(client_id)
            if client is None:
                raise NotFoundError("Client not found.")
            changes = self.onboarding._apply_update_brand(client, BrandUpdate(**payload["data"]))
            op.field_changes = changes
            return client.id, client.name
        if op.entity_type == "user":
            target_user, changes = self.user_service._apply_update_user(
                op.entity_id, UserUpdate(**payload["data"])
            )
            op.field_changes = changes
            return target_user.id, target_user.email
        raise BadRequestError(f"Unknown proposal entity type: {op.entity_type}")

    def _execute_plan_task(
        self, client_id: uuid.UUID, op: ProposedOperation, payload: dict
    ) -> tuple[uuid.UUID | None, str | None]:
        if op.operation_type == ProposedOperationType.create:
            if "source_task_id" in payload:
                task, changes = self.plans._apply_duplicate_task(
                    client_id,
                    uuid.UUID(payload["source_task_id"]),
                    created_by=uuid.UUID(payload["created_by"]),
                )
            else:
                task, changes = self.plans._apply_create_task(
                    client_id,
                    PlanTaskCreate(**payload["data"]),
                    created_by=uuid.UUID(payload["created_by"]),
                )
            op.field_changes = changes
            return task.id, task.title
        if op.operation_type == ProposedOperationType.update:
            task, changes = self.plans._apply_update_task(
                client_id, op.entity_id, PlanTaskUpdate(**payload["data"])
            )
            op.field_changes = changes
            return task.id, task.title
        if op.operation_type == ProposedOperationType.delete:
            task, changes = self.plans._apply_delete_task(client_id, op.entity_id)
            op.field_changes = changes
            return op.entity_id, task.title
        raise BadRequestError(f"Unsupported plan_task operation: {op.operation_type.value}")

    def _execute_assignment(
        self, client_id: uuid.UUID, op: ProposedOperation, payload: dict
    ) -> tuple[uuid.UUID | None, str | None]:
        target_user_id = uuid.UUID(payload["user_id"])
        if op.operation_type == ProposedOperationType.assign:
            caps = (
                [ClientCapability(c) for c in payload["capabilities"]]
                if payload.get("capabilities")
                else None
            )
            assigned_by = uuid.UUID(payload["assigned_by"])
            assignment = self.assignments._apply_assign(
                client_id, target_user_id, assigned_by=assigned_by, capabilities=caps
            )
            return assignment.id, None
        if op.operation_type == ProposedOperationType.update:
            caps = [ClientCapability(c) for c in payload["capabilities"]]
            assignment = self.assignments._apply_set_capabilities(client_id, target_user_id, caps)
            return assignment.id, None
        if op.operation_type == ProposedOperationType.unassign:
            self.assignments._apply_unassign(client_id, target_user_id)
            return op.entity_id, None
        raise BadRequestError(f"Unsupported client_assignment operation: {op.operation_type.value}")
