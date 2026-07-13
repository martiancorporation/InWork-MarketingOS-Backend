"""The build pipeline: ingest → chunk+embed → summarize → extract directives →
reconcile → commit a new profile version atomically.

Full builds re-extract & re-embed everything; incremental builds reuse unchanged
sources' chunks (via content-hash skipping) but always recompute the summary and
directives from the *complete* current corpus, so conflicts are re-reconciled
against everything. On success the client's ``current_profile_version`` pointer
is flipped in one commit; a failure never touches the live profile.

Dependencies (embedder, agents, storage) are injected so the worker wires real
ones and tests inject fakes.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.ai.directives import DirectivesAgent
from app.ai.summary import SummaryAgent
from app.ai.usage import AiUsageContext
from app.core.config import get_settings
from app.integrations.embeddings.base import EmbeddingClient
from app.integrations.storage import Storage
from app.models.client import Client
from app.models.client_directive import ClientDirective
from app.models.client_profile import ClientProfile
from app.models.enums import IntelJobType, ProfileStatus, SourceStatus
from app.models.knowledge import KnowledgeChunk
from app.repositories.client_profile_repository import ClientProfileRepository
from app.repositories.knowledge_repository import KnowledgeChunkRepository
from app.services.intelligence.chunking_service import ChunkingService
from app.services.intelligence.ingestion_service import IngestionService
from app.services.intelligence.reconcile import merge_capability_flags, reconcile

logger = logging.getLogger("app.intelligence.orchestrator")


class IntelligenceOrchestrator:
    def __init__(
        self,
        db: Session,
        *,
        embedder: EmbeddingClient,
        storage: Storage | None = None,
        summary_agent: SummaryAgent | None = None,
        directives_agent: DirectivesAgent | None = None,
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.storage = storage
        self.summary_agent = summary_agent or SummaryAgent()
        self.directives_agent = directives_agent or DirectivesAgent()
        self.chunker = ChunkingService()
        self.profiles = ClientProfileRepository(db)
        self.chunks = KnowledgeChunkRepository(db)
        self.settings = get_settings().intelligence

    async def build(
        self,
        client: Client,
        *,
        job_type: str = IntelJobType.full_build.value,
        changed_keys: set[str] | None = None,
        created_by: uuid.UUID | None = None,
    ) -> ClientProfile:
        full = job_type == IntelJobType.full_build.value
        ctx = AiUsageContext(feature="intelligence.build", client_id=client.id)

        # 1. Ingest sources (fields + files), extract text, hash for change detection.
        ingestion = IngestionService(self.db, self.storage)
        synced = ingestion.sync(client, full=full, changed_keys=changed_keys)

        # 2. (Re)chunk + embed only changed/new sources; reuse the rest.
        source_by_key: dict[str, uuid.UUID] = {}
        for item in synced:
            src = item.source
            source_by_key[_source_key(src)] = src.id
            if item.needs_rechunk:
                self.chunks.delete_for_source(src.id)
                if src.status == SourceStatus.extracted.value and (src.extracted_text or "").strip():
                    self._embed_source(client.id, src)

        # 3. Assemble the full corpus (all current sources, changed or not).
        corpus = self._assemble_corpus(synced)

        # 4 & 5. Summary + directives over the complete corpus.
        summary = await self.summary_agent.summarize(client, corpus, ctx)
        raw_directives = await self.directives_agent.extract(
            client, corpus, summary.profile, ctx
        )

        # 6. Reconcile conflicts + compile capability flags.
        reconciled = reconcile(raw_directives)
        capability_flags = merge_capability_flags(reconciled)

        # 7. New profile version.
        version = self.profiles.next_version(client.id)
        profile = ClientProfile(
            client_id=client.id,
            version=version,
            status=ProfileStatus.ready.value,
            summary_md=summary.summary_md,
            profile=summary.profile,
            capability_flags=capability_flags,
            model=summary.model,
            source_hashes={_source_key(i.source): i.source.content_hash for i in synced},
            created_by=created_by,
        )
        self.profiles.add(profile)
        self.db.flush()  # assign profile.id

        # 8. Directive rows (two-pass so conflicts_with_id can reference siblings).
        created: list[ClientDirective] = []
        for r in reconciled:
            d = r.directive
            row = ClientDirective(
                profile_id=profile.id,
                client_id=client.id,
                type=d.type,
                category=d.category,
                text=d.text,
                tier=d.tier,
                rank=d.rank,
                confidence=d.confidence,
                status=r.status,
                capability_flags=d.capability_flags or None,
                source_id=source_by_key.get(d.source_key) if d.source_key else None,
            )
            self.db.add(row)
            created.append(row)
        self.db.flush()
        for r, row in zip(reconciled, created):
            if r.conflicts_with_index is not None:
                row.conflicts_with_id = created[r.conflicts_with_index].id

        # 9. Supersede the previous version and flip the pointer atomically.
        if client.current_profile_version is not None:
            prev = self.profiles.get_version(client.id, client.current_profile_version)
            if prev is not None and prev.id != profile.id:
                prev.status = ProfileStatus.superseded.value
        client.current_profile_version = version

        self.db.commit()
        logger.info(
            "Built profile v%s for client %s (%s directives, %s sources)",
            version, client.id, len(created), len(synced),
        )
        return profile

    # ---- helpers ----

    def _embed_source(self, client_id: uuid.UUID, src) -> None:
        pieces = self.chunker.chunk(src.extracted_text)
        if not pieces:
            return
        vectors = self.embedder.embed([p.text for p in pieces], input_type="document")
        weight = 2.0 if src.ref_key in {"brand", "compliance"} else 1.0
        for piece, vec in zip(pieces, vectors):
            self.db.add(
                KnowledgeChunk(
                    client_id=client_id,
                    source_id=src.id,
                    ordinal=piece.ordinal,
                    text=piece.text,
                    char_count=len(piece.text),
                    token_estimate=len(piece.text) // 4,
                    embedding=vec,
                    weight=weight,
                    meta={"label": src.label},
                )
            )
        src.status = SourceStatus.embedded.value

    def _assemble_corpus(self, synced) -> str:
        parts: list[str] = []
        total = 0
        cap = self.settings.max_corpus_chars
        # Fields before documents; brand/compliance/goals surface first.
        priority = {"field": 0, "document": 1}
        ordered = sorted(
            synced, key=lambda i: priority.get(i.source.ref_kind or "document", 1)
        )
        for item in ordered:
            src = item.source
            text = (src.extracted_text or "").strip()
            if not text:
                continue
            block = f"## {src.label}\n{text}"
            if total + len(block) > cap:
                block = block[: max(0, cap - total)]
            parts.append(block)
            total += len(block)
            if total >= cap:
                break
        return "\n\n".join(parts)


def _source_key(src) -> str:
    if src.ref_kind == "document" and src.ref_id:
        return f"document:{src.ref_id}"
    return f"field:{src.ref_key}"
