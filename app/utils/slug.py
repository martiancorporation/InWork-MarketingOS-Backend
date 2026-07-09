"""Slug helpers."""

from __future__ import annotations

import re
from collections.abc import Callable

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, fallback: str = "item") -> str:
    slug = _NON_SLUG.sub("-", text.lower()).strip("-")
    return slug or fallback


def unique_slug(base: str, *, exists: Callable[[str], bool]) -> str:
    """Return ``base`` (or ``base-2``, ``base-3``, …) — the first not taken.

    ``exists`` reports whether a candidate slug is already used.
    """
    candidate = base
    counter = 2
    while exists(candidate):
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate
