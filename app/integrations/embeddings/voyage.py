"""Voyage AI embedding backend (Anthropic's recommended embeddings partner).

The ``voyageai`` SDK is imported lazily so the app runs without it installed.
Raises ``ServiceUnavailableError`` when called without a key so callers can fall
back to the deterministic embedder.
"""

from __future__ import annotations

from app.core.exceptions import ServiceUnavailableError


class VoyageEmbedder:
    def __init__(self, api_key: str | None, model: str, dim: int) -> None:
        self._api_key = api_key
        self._model = model
        self._dim = dim
        self._client_obj = None

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _client(self):
        if not self._api_key:
            raise ServiceUnavailableError("Voyage embeddings are not configured.")
        if self._client_obj is None:
            try:
                import voyageai
            except ImportError as exc:  # pragma: no cover - optional dep
                raise ServiceUnavailableError(
                    "The 'voyageai' package is required for embeddings."
                ) from exc
            self._client_obj = voyageai.Client(api_key=self._api_key)
        return self._client_obj

    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        if not texts:
            return []
        result = self._client().embed(texts, model=self._model, input_type=input_type)
        return [list(v) for v in result.embeddings]
