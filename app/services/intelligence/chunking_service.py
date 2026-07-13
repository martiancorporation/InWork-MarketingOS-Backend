"""Split extracted text into overlapping, embeddable chunks.

Character-based windows (no tokenizer dependency) that prefer to break on
paragraph/sentence boundaries. ~4 chars ≈ 1 token, so the defaults (2400 chars,
320 overlap) target ~600-token chunks with ~13% overlap.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str


class ChunkingService:
    def __init__(self, size: int | None = None, overlap: int | None = None) -> None:
        s = get_settings().intelligence
        self.size = size or s.chunk_chars
        self.overlap = overlap if overlap is not None else s.chunk_overlap_chars

    def chunk(self, text: str) -> list[Chunk]:
        text = (text or "").strip()
        if not text:
            return []
        if len(text) <= self.size:
            return [Chunk(0, text)]

        chunks: list[Chunk] = []
        start = 0
        ordinal = 0
        n = len(text)
        while start < n:
            end = min(start + self.size, n)
            if end < n:
                end = self._soft_break(text, start, end)
            piece = text[start:end].strip()
            if piece:
                chunks.append(Chunk(ordinal, piece))
                ordinal += 1
            if end >= n:
                break
            start = max(end - self.overlap, start + 1)
        return chunks

    def _soft_break(self, text: str, start: int, end: int) -> int:
        """Nudge the cut back to the nearest paragraph/sentence/space boundary."""
        window = text[start:end]
        for sep in ("\n\n", "\n", ". ", " "):
            idx = window.rfind(sep)
            if idx > self.size // 2:
                return start + idx + len(sep)
        return end
