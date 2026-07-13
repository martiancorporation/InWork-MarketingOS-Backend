"""Deterministic local embedder — no network, no API key.

Used automatically when the real provider is unconfigured (and in tests). It
hashes token features into a fixed-dimension L2-normalized vector, so identical
text yields identical vectors and lexically-overlapping text lands nearby —
enough for the pipeline to run end-to-end and for retrieval tests to be
meaningful. NOT a substitute for real embeddings in production quality.
"""

from __future__ import annotations

import hashlib
import math
import re

_TOKEN = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def is_configured(self) -> bool:
        return True

    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        tokens = _TOKEN.findall((text or "").lower())
        for tok in tokens:
            h = hashlib.sha1(tok.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "big") % self._dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]
