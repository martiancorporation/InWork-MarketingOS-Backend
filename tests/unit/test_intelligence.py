"""Unit tests for the intelligence building blocks (no DB)."""

from __future__ import annotations

import asyncio

from app.ai.directives import Directive, DirectivesAgent
from app.integrations.embeddings.fake import FakeEmbedder
from app.models.client import Client
from app.models.enums import DirectiveStatus, DirectiveTier, DirectiveType
from app.services.intelligence.chunking_service import ChunkingService
from app.services.intelligence.reconcile import merge_capability_flags, reconcile

# ---- chunking ----

def test_chunking_splits_long_text_with_overlap() -> None:
    chunker = ChunkingService(size=100, overlap=20)
    text = "word " * 200  # ~1000 chars
    chunks = chunker.chunk(text)
    assert len(chunks) > 1
    assert all(c.text for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_chunking_short_text_single_chunk() -> None:
    assert len(ChunkingService(size=100, overlap=10).chunk("short")) == 1
    assert ChunkingService().chunk("") == []


# ---- embeddings ----

def test_fake_embedder_deterministic_and_normalized() -> None:
    e = FakeEmbedder(dim=64)
    a1 = e.embed(["hello world"])[0]
    a2 = e.embed(["hello world"])[0]
    assert a1 == a2  # deterministic
    assert len(a1) == 64
    norm = sum(x * x for x in a1) ** 0.5
    assert abs(norm - 1.0) < 1e-6  # L2-normalized


def test_fake_embedder_overlap_closer_than_unrelated() -> None:
    e = FakeEmbedder(dim=256)
    base, similar, other = e.embed([
        "brand voice friendly warm home goods",
        "brand voice friendly warm decor",
        "quarterly revenue tax spreadsheet",
    ])
    dot = lambda x, y: sum(a * b for a, b in zip(x, y))  # noqa: E731
    assert dot(base, similar) > dot(base, other)


# ---- capability net (the "no AI-generated text" requirement) ----

def test_capability_net_flags_no_ai_text() -> None:
    client = Client(name="Acme", brand_voice="Friendly")
    corpus = "Onboarding notes: we do not want AI-generated text anywhere in our ads."
    directives = asyncio.run(DirectivesAgent().extract(client, corpus))
    flagged = [d for d in directives if (d.capability_flags or {}).get("ai_text_generation") is False]
    assert flagged, [d.text for d in directives]
    assert flagged[0].type == DirectiveType.must_not.value
    assert flagged[0].tier == DirectiveTier.mandatory.value


# ---- reconcile ----

def test_reconcile_flags_conflicting_directives() -> None:
    directives = [
        Directive(type=DirectiveType.must_not.value, category="content",
                  text="Never use the word cheap", tier=DirectiveTier.mandatory.value, rank=0),
        Directive(type=DirectiveType.prefer.value, category="content",
                  text="Use the word cheap in promos", tier=DirectiveTier.preference.value, rank=40),
    ]
    result = reconcile(directives)
    by_type = {r.directive.type: r for r in result}
    assert by_type[DirectiveType.must_not.value].status == DirectiveStatus.active.value
    conflicted = by_type[DirectiveType.prefer.value]
    assert conflicted.status == DirectiveStatus.conflicted.value
    assert conflicted.conflicts_with_index is not None


def test_merge_capability_flags_false_wins() -> None:
    d = Directive(type=DirectiveType.must_not.value, category="content", text="no ai",
                  tier=DirectiveTier.mandatory.value, rank=0,
                  capability_flags={"ai_text_generation": False})
    flags = merge_capability_flags(reconcile([d]))
    assert flags == {"ai_text_generation": False}
