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
from app.ai.features import AiFeature
from app.ai.summary import SummaryAgent
from app.ai.usage import AiUsageContext
from app.core.config import get_settings
from app.integrations.embeddings.base import EmbeddingClient
from app.integrations.storage import Storage
from app.models.client import Client
from app.models.client_directive import ClientDirective
from app.models.client_profile import ClientProfile
from app.models.enums import (
    DirectiveStatus,
    DirectiveType,
    IntelJobType,
    ProfileStatus,
    SourceStatus,
)
from app.models.knowledge import KnowledgeChunk
from app.repositories.client_profile_repository import (
    ClientDirectiveRepository,
    ClientProfileRepository,
)
from app.repositories.knowledge_repository import KnowledgeChunkRepository
from app.services.intelligence.chunking_service import ChunkingService
from app.services.intelligence.ingestion_service import IngestionService
from app.services.intelligence.reconcile import Reconciled, merge_capability_flags, reconcile

logger = logging.getLogger("app.intelligence.orchestrator")

# Directive types powerful enough to warrant a human sign-off the first time
# they appear: they become the enforced "HARD RULES" preamble and can flip
# capability flags off outright.
_REVIEW_GATED_TYPES = frozenset({DirectiveType.must.value, DirectiveType.must_not.value})


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
        self.directives = ClientDirectiveRepository(db)
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
        # Two distinct usage labels (not one shared "intelligence.build") so
        # ai_usage_events — and therefore cost_optimization.py's per-feature
        # savings suggestions — can tell summary-generation spend apart from
        # directive-extraction spend instead of lumping both into one bucket.
        summary_ctx = AiUsageContext(feature=AiFeature.CLIENT_SUMMARY, client_id=client.id)
        directives_ctx = AiUsageContext(feature=AiFeature.CLIENT_DIRECTIVES, client_id=client.id)

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
                if (
                    src.status == SourceStatus.extracted.value
                    and (src.extracted_text or "").strip()
                ):
                    self._embed_source(client.id, src)

        # 3. Assemble the full corpus (all current sources, changed or not).
        corpus = self._assemble_corpus(synced)

        # 4 & 5. Summary + directives over the complete corpus.
        summary = await self.summary_agent.summarize(client, corpus, summary_ctx)
        raw_directives = await self.directives_agent.extract(
            client, corpus, summary.profile, directives_ctx
        )

        # 6. Reconcile conflicts, gate *newly-seen* must/must_not directives
        # behind review, then compile capability flags.
        reconciled = reconcile(raw_directives)
        self._gate_new_restrictions(client, reconciled)
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
            version,
            client.id,
            len(created),
            len(synced),
        )
        return profile

    # ---- helpers ----

    def _gate_new_restrictions(self, client: Client, reconciled: list[Reconciled]) -> None:
        """Hold *newly-seen* ``must``/``must_not`` directives at ``pending_review``.

        These two types become binding, machine-enforced rules — they land in
        the "HARD RULES" preamble every agent gets, and can flip a capability
        flag off outright (e.g. ``{"ai_text_generation": false}``). They're
        extracted by a model from content the client supplied, and a genuine
        client preference is textually indistinguishable from an instruction
        injected into one of their documents. So the first time a given rule
        appears it waits for an admin (``POST .../directives/{id}/resolve``,
        the same endpoint ``conflicted`` already uses).

        Crucially this only gates rules that are *new*. A rule already approved
        on the client's current profile is carried forward as ``active``,
        because a rebuild fires on routine edits (an onboarding autosave, a
        compliance note) — re-gating on every rebuild would silently stop
        enforcing a restriction the client asked for and an admin already
        signed off, which fails open in exactly the direction that matters.
        Identity is the ``(type, normalized text)`` pair ``reconcile()``
        already dedupes on, so re-wording a rule correctly requires a fresh
        review. Conflicted directives keep that status — they need resolution
        either way.
        """
        approved = self._approved_directive_keys(client)
        for r in reconciled:
            if r.status != DirectiveStatus.active.value:
                continue
            if not _needs_review(r.directive):
                continue
            if _directive_key(r.directive.type, r.directive.text) in approved:
                continue
            r.status = DirectiveStatus.pending_review.value

    def _approved_directive_keys(self, client: Client) -> set[tuple[str, str]]:
        """``(type, normalized text)`` of every directive currently ``active``
        on the client's live profile — i.e. already approved (or predating the
        review gate). Empty for a client's very first build."""
        version = client.current_profile_version
        if version is None:
            return set()
        current = self.profiles.get_version(client.id, version)
        if current is None:
            return set()
        return {
            _directive_key(d.type, d.text)
            for d in self.directives.active_for_profile(current.id)
            if d.status == DirectiveStatus.active.value
        }

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
        ordered = sorted(synced, key=lambda i: priority.get(i.source.ref_kind or "document", 1))
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


def _directive_key(directive_type: str, text: str) -> tuple[str, str]:
    """Identity that carries an approval across rebuilds — deliberately the
    same ``(type, normalized text)`` pair ``reconcile()`` dedupes on, so the
    two can't drift apart."""
    return (directive_type, (text or "").strip().lower())


def _needs_review(directive) -> bool:
    """Whether a directive is powerful enough to need a human sign-off.

    Type alone isn't the right test: ``merge_capability_flags`` honours
    ``capability_flags`` from *any* active directive, so gating only
    ``must``/``must_not`` would let the extractor bypass review entirely just
    by labelling a rule ``constraint`` or ``avoid`` — the flag would go live
    unreviewed. Gate on the actual power a directive carries: it's a hard rule
    by type, or it flips a capability off.
    """
    return directive.type in _REVIEW_GATED_TYPES or bool(directive.capability_flags)
