"""Read/management use-cases for client intelligence (profiles, directives).

The async build happens in the worker; this service serves the results and
handles admin actions (force rebuild, resolve a flagged conflict).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.client import Client
from app.models.enums import (
    DirectiveStatus,
    IntelJobStatus,
    IntelJobType,
    ProfileStatus,
)
from app.repositories.client_profile_repository import (
    ClientDirectiveRepository,
    ClientProfileRepository,
)
from app.repositories.intel_job_repository import IntelJobRepository
from app.schemas.intelligence import (
    ClientProfileRead,
    DirectiveRead,
    IntelligenceResponse,
    IntelligenceStatus,
    ProfileVersionItem,
)
from app.services.intelligence.job_queue import JobQueue


class IntelligenceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.profiles = ClientProfileRepository(db)
        self.directives = ClientDirectiveRepository(db)
        self.jobs = IntelJobRepository(db)

    def status(self, client: Client) -> IntelligenceStatus:
        job = self.jobs.latest_for_client(client.id)
        job_status = job.status if job else None
        version = client.current_profile_version
        profile = self.profiles.get_version(client.id, version) if version else None

        if profile is not None and profile.status == ProfileStatus.ready.value:
            state = "ready"
        elif job_status in {IntelJobStatus.queued.value, IntelJobStatus.running.value}:
            state = "building"
        elif job_status in {IntelJobStatus.failed.value, IntelJobStatus.dead.value}:
            state = "failed"
        else:
            state = "none"
        return IntelligenceStatus(
            status=state,
            version=version,
            job_status=job_status,
            updated_at=profile.created_at if profile else None,
        )

    def get_current(self, client: Client) -> IntelligenceResponse:
        version = client.current_profile_version
        if version is None:
            return IntelligenceResponse(status=self.status(client).status)
        return self._response(client, version)

    def get_version(self, client: Client, version: int) -> IntelligenceResponse:
        return self._response(client, version)

    def versions(self, client: Client) -> list[ProfileVersionItem]:
        return [
            ProfileVersionItem(version=p.version, status=p.status, created_at=p.created_at)
            for p in self.profiles.list_versions(client.id)
        ]

    def rebuild(self, client: Client) -> IntelligenceStatus:
        JobQueue(self.db).enqueue(client.id, IntelJobType.full_build.value)
        self.db.commit()
        return self.status(client)

    def resolve_directive(
        self, client: Client, directive_id: uuid.UUID, activate: bool
    ) -> DirectiveRead:
        directive = self.directives.get_owned(client.id, directive_id)
        if directive is None:
            raise NotFoundError("Directive not found.")
        directive.status = (
            DirectiveStatus.active.value if activate else DirectiveStatus.superseded.value
        )
        self.db.commit()
        self.db.refresh(directive)
        return DirectiveRead.model_validate(directive)

    def _response(self, client: Client, version: int) -> IntelligenceResponse:
        profile = self.profiles.get_version(client.id, version)
        if profile is None:
            raise NotFoundError("Profile version not found.")
        directives = self.directives.active_for_profile(profile.id)
        return IntelligenceResponse(
            status=profile.status,
            version=profile.version,
            profile=ClientProfileRead.model_validate(profile),
            directives=[DirectiveRead.model_validate(d) for d in directives],
        )
