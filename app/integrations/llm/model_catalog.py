"""Live OpenRouter model catalog — the single source of truth for "what
models exist and what do they cost", replacing every hand-maintained model
list/price table in this codebase.

OpenRouter's ``GET /models`` is public (no API key required) and returns every
model it proxies across every vendor (Anthropic, OpenAI, Google, DeepSeek,
Qwen, ...) with live per-token pricing and context length — see
https://openrouter.ai/api/v1/models. Fetching it directly means the admin's
model picker and the pricing used to bill usage always reflect exactly what's
actually available and what it actually costs today, with no code change
needed when the provider adds, renames, or re-prices a model.

Cached in-process (``_CACHE_TTL_SECONDS``) since this is consulted on every
priced AI call (via ``app/ai/pricing.py``), not just when an admin opens the
routing page — a synchronous ``httpx`` call (this module has no async
callers) so both the admin router (plain ``def`` handlers) and the
thread-offloaded usage-pricing path (``app/ai/usage.py::record_usage``, called
via ``anyio.to_thread.run_sync``) can use it without an async/sync bridge.

Graceful degradation, same stance as every other AI feature in this repo: a
fetch failure never raises — it logs a warning and returns the last good
cache, or an empty list on a cold start with no network yet.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx

logger = logging.getLogger(__name__)

_CATALOG_URL = "https://openrouter.ai/api/v1/models"
_TIMEOUT_SECONDS = 10.0
# The catalog and its pricing change rarely (new model releases, not
# minute-to-minute) — long enough to keep this off the hot path in steady
# state, short enough that a newly-released model shows up in the admin
# picker within the hour without a restart.
_CACHE_TTL_SECONDS = 3600.0


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    context_length: int | None
    input_per_million: Decimal | None
    output_per_million: Decimal | None
    cache_write_per_million: Decimal | None
    cache_read_per_million: Decimal | None


_cache: list[CatalogModel] | None = None
_cache_loaded_at: float = 0.0


def invalidate_cache() -> None:
    global _cache
    _cache = None


def _per_million(raw: object) -> Decimal | None:
    """OpenRouter prices per single token, as a decimal string (e.g.
    ``"0.0000002"``). A negative value (e.g. ``"-1"``, seen on meta/auto-router
    models with variable, per-request pricing) means "not a fixed rate" —
    treated as unknown rather than a literal negative cost."""
    if not isinstance(raw, str):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if value < 0:
        return None
    return value * 1_000_000


def _fetch() -> list[CatalogModel]:
    resp = httpx.get(_CATALOG_URL, timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    payload = resp.json()
    models: list[CatalogModel] = []
    for row in payload.get("data") or []:
        model_id = row.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        pricing = row.get("pricing") or {}
        models.append(
            CatalogModel(
                id=model_id,
                name=str(row.get("name") or model_id),
                context_length=row.get("context_length"),
                input_per_million=_per_million(pricing.get("prompt")),
                output_per_million=_per_million(pricing.get("completion")),
                cache_write_per_million=_per_million(pricing.get("input_cache_write")),
                cache_read_per_million=_per_million(pricing.get("input_cache_read")),
            )
        )
    return models


def get_catalog(*, force_refresh: bool = False) -> list[CatalogModel]:
    """The live model catalog, cached for ``_CACHE_TTL_SECONDS``. Never
    raises — see the module docstring."""
    global _cache, _cache_loaded_at
    now = time.monotonic()
    if not force_refresh and _cache is not None and (now - _cache_loaded_at) < _CACHE_TTL_SECONDS:
        return _cache
    try:
        models = _fetch()
    except Exception:
        logger.warning("Failed to fetch the OpenRouter model catalog", exc_info=True)
        return _cache or []
    _cache = models
    _cache_loaded_at = now
    return _cache


def get_model(model_id: str) -> CatalogModel | None:
    """One catalog entry by id, or ``None`` if it isn't (or isn't currently)
    a real OpenRouter model."""
    for m in get_catalog():
        if m.id == model_id:
            return m
    return None


def cheapest_model() -> CatalogModel | None:
    """The lowest-input-cost priced model currently in the catalog — used as
    a deliberately conservative, non-hardcoded bootstrap pick for a task
    category nobody has configured yet (see
    ``app.services.ai_model_route_service.AiModelRouteService.list_routes``).
    Not a quality recommendation, just a safe, cost-minimal starting point
    until an admin actually tunes it."""
    priced = [m for m in get_catalog() if m.input_per_million is not None]
    if not priced:
        return None
    return min(priced, key=lambda m: (m.input_per_million, m.id))
