"""Gather every client input into hashed ``KnowledgeSource`` rows.

Sources are (a) onboarding field groups rendered to text and (b) uploaded
documents (downloaded from S3 and text-extracted). A ``content_hash`` per source
drives incremental rebuilds: unchanged sources are reused (documents aren't even
re-downloaded), changed/new ones are re-extracted and flagged for re-chunking,
and sources that disappeared (deleted files/fields) are removed.

All extracted text is untrusted data — it is analyzed, never executed.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.integrations.documents import extract_text
from app.integrations.storage import Storage
from app.models.client import Client
from app.models.enums import KnowledgeSourceType, SourceStatus
from app.models.knowledge import KnowledgeSource
from app.repositories.knowledge_repository import (
    KnowledgeChunkRepository,
    KnowledgeSourceRepository,
)


@dataclass
class _Desired:
    identity: str
    source_type: str
    label: str
    hash_basis: str
    load_text: Callable[[], tuple[str, str]]  # -> (text, status)
    ref_kind: str | None = None
    ref_id: uuid.UUID | None = None
    ref_key: str | None = None


@dataclass
class SyncedSource:
    source: KnowledgeSource
    needs_rechunk: bool


class IngestionService:
    def __init__(self, db: Session, storage: Storage | None = None) -> None:
        self.db = db
        self.storage = storage
        self.sources = KnowledgeSourceRepository(db)
        self.chunks = KnowledgeChunkRepository(db)
        self._max_doc = get_settings().intelligence.max_document_chars

    def sync(
        self, client: Client, *, full: bool, changed_keys: set[str] | None = None
    ) -> list[SyncedSource]:
        desired = self._desired(client)
        existing = {self._identity(s): s for s in self.sources.list_for_client(client.id)}
        seen: set[str] = set()
        result: list[SyncedSource] = []

        for d in desired:
            seen.add(d.identity)
            src = existing.get(d.identity)
            new_hash = _sha(d.hash_basis)
            changed = src is None or src.content_hash != new_hash or full

            if not changed:
                result.append(SyncedSource(src, needs_rechunk=False))
                continue

            text, status = d.load_text()
            text = text[: self._max_doc]
            if src is None:
                src = KnowledgeSource(
                    client_id=client.id,
                    source_type=d.source_type,
                    ref_kind=d.ref_kind,
                    ref_id=d.ref_id,
                    ref_key=d.ref_key,
                    label=d.label,
                )
                self.sources.add(src)
            src.content_hash = new_hash
            src.extracted_text = text
            src.char_count = len(text)
            src.status = status
            result.append(SyncedSource(src, needs_rechunk=True))

        # Remove sources whose origin no longer exists (deleted file/field).
        for identity, src in existing.items():
            if identity not in seen:
                self.db.delete(src)

        self.db.flush()
        return result

    # ---- desired-source assembly ----

    def _desired(self, client: Client) -> list[_Desired]:
        out: list[_Desired] = []
        for key, text in _field_groups(client):
            out.append(
                _Desired(
                    identity=f"field:{key}",
                    source_type=KnowledgeSourceType.onboarding_field.value,
                    ref_kind="field",
                    ref_key=key,
                    label=f"Onboarding: {key}",
                    hash_basis=text,
                    load_text=lambda t=text: (t, SourceStatus.extracted.value),
                )
            )
        for doc in client.documents:
            out.append(
                _Desired(
                    identity=f"document:{doc.id}",
                    source_type=KnowledgeSourceType.document.value,
                    ref_kind="document",
                    ref_id=doc.id,
                    label=doc.name,
                    # Hash the immutable storage key — a replaced file is a new
                    # key, so unchanged docs are never re-downloaded.
                    hash_basis=doc.storage_url or str(doc.id),
                    load_text=lambda d=doc: self._load_document(d),
                )
            )
        return out

    def _load_document(self, doc) -> tuple[str, str]:
        if self.storage is None:
            return "", SourceStatus.failed.value
        try:
            data = self.storage.download(doc.storage_url)
        except AppError:
            return "", SourceStatus.failed.value
        result = extract_text(data, doc.mime_type, doc.name)
        return result.text, result.status

    @staticmethod
    def _identity(src: KnowledgeSource) -> str:
        if src.ref_kind == "document" and src.ref_id:
            return f"document:{src.ref_id}"
        return f"field:{src.ref_key}"


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _field_groups(client: Client) -> list[tuple[str, str]]:
    """Render non-empty onboarding field groups to plain text."""
    groups: list[tuple[str, str]] = []

    basics = _lines(
        ("Name", client.name),
        ("Business type", client.business_type),
        ("Industry", client.industry),
        ("Website", client.website),
        ("Language", client.language),
        ("Location", client.location),
    )
    if basics:
        groups.append(("basics", basics))

    if (client.markets or "").strip():
        groups.append(("markets", f"Operating markets:\n{client.markets.strip()}"))

    brand_parts = _lines(
        ("About the brand", client.about_brand),
        ("Brand voice", client.brand_voice),
        ("Extracted brand theme", client.brand_extracted),
        ("Color guidelines", client.color_guidelines),
    )
    colors = ", ".join(
        f"{c.hex}" + (f" ({c.label})" if c.label else "") for c in client.brand_colors
    )
    fonts = ", ".join(f.family for f in client.brand_fonts)
    if colors:
        brand_parts += f"\nBrand colors: {colors}"
    if fonts:
        brand_parts += f"\nBrand fonts: {fonts}"
    if brand_parts.strip():
        groups.append(("brand", brand_parts.strip()))

    if (client.goals or "").strip():
        groups.append(("goals", f"Client goals:\n{client.goals.strip()}"))

    compliance = "\n".join(
        f"- [{e.kind.value if hasattr(e.kind, 'value') else e.kind}] {e.text}"
        for e in client.compliance_entries
    )
    if compliance.strip():
        groups.append(("compliance", f"Compliance & rules:\n{compliance}"))

    contacts = "\n".join(
        f"- {c.name or '?'} ({c.role or 'contact'}, {c.department or '-'}) "
        f"{c.email or ''} [{c.side.value if hasattr(c.side, 'value') else c.side}]"
        for c in client.contacts
    )
    if contacts.strip():
        groups.append(("contacts", f"Points of contact:\n{contacts}"))

    return groups


def _lines(*pairs: tuple[str, str | None]) -> str:
    return "\n".join(f"{label}: {value}" for label, value in pairs if (value or "").strip())
