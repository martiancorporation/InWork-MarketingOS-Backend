"""Client-assignment use-cases (admin only — enforced at the router).

Assigning a client to a user is what grants that non-admin user access to it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models.assignment import ClientAssignment
from app.repositories.assignment_repository import AssignmentRepository
from app.repositories.client_repository import ClientRepository
from app.repositories.user_repository import UserRepository
from app.schemas.assignment import AssignmentListResponse, AssignmentRead
from app.schemas.user import UserRead


class AssignmentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.assignments = AssignmentRepository(db)
        self.clients = ClientRepository(db)
        self.users = UserRepository(db)

    def assign(
        self, client_id: uuid.UUID, user_id: uuid.UUID, *, assigned_by: uuid.UUID
    ) -> ClientAssignment:
        if self.clients.get(client_id) is None:
            raise NotFoundError("Client not found.")
        if self.users.get(user_id) is None:
            raise NotFoundError("User not found.")
        if self.assignments.exists(client_id, user_id):
            raise ConflictError("This client is already assigned to that user.")

        assignment = ClientAssignment(
            client_id=client_id, user_id=user_id, assigned_by=assigned_by
        )
        self.db.add(assignment)
        self.db.commit()
        self.db.refresh(assignment)
        return assignment

    def unassign(self, client_id: uuid.UUID, user_id: uuid.UUID) -> None:
        assignment = self.assignments.get(client_id, user_id)
        if assignment is None:
            raise NotFoundError("Assignment not found.")
        self.db.delete(assignment)
        self.db.commit()

    def list_for_client(self, client_id: uuid.UUID) -> AssignmentListResponse:
        if self.clients.get(client_id) is None:
            raise NotFoundError("Client not found.")
        rows = self.assignments.list_for_client(client_id)
        items = [
            AssignmentRead(
                client_id=a.client_id,
                assigned_by=a.assigned_by,
                created_at=a.created_at,
                user=UserRead.model_validate(a.user),
            )
            for a in rows
        ]
        return AssignmentListResponse(items=items, total=len(items))
