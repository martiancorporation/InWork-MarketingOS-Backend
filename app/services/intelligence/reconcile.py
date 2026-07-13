"""Deduplicate, detect conflicts, and compile capability flags for directives.

Conflict rule: within one category, a mandatory ``must_not`` opposing a
``must``/``prefer`` with high lexical overlap is a conflict. The more
restrictive rule stays active; the weaker one is flagged ``conflicted`` for
human review and never silently applied (fail-safe toward the restriction).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.ai.directives import Directive
from app.models.enums import DirectiveStatus, DirectiveType

_WORD = re.compile(r"[a-z0-9]+")
_OVERLAP_THRESHOLD = 0.5


@dataclass
class Reconciled:
    directive: Directive
    status: str
    conflicts_with_index: int | None = None


def reconcile(directives: list[Directive]) -> list[Reconciled]:
    # Dedupe by (type, normalized text).
    seen: set[tuple[str, str]] = set()
    unique: list[Directive] = []
    for d in directives:
        key = (d.type, d.text.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(d)

    # Highest priority first (lowest rank) so restrictions are the "anchor".
    order = sorted(range(len(unique)), key=lambda i: unique[i].rank)
    result: list[Reconciled] = [Reconciled(d, DirectiveStatus.active.value) for d in unique]

    for ai in range(len(order)):
        i = order[ai]
        a = unique[i]
        if a.type != DirectiveType.must_not.value:
            continue
        a_tokens = _tokens(a.text)
        for bj in range(ai + 1, len(order)):
            j = order[bj]
            b = unique[j]
            if result[j].status != DirectiveStatus.active.value:
                continue
            if b.category != a.category:
                continue
            if b.type in {DirectiveType.must.value, DirectiveType.prefer.value}:
                if _jaccard(a_tokens, _tokens(b.text)) >= _OVERLAP_THRESHOLD:
                    result[j].status = DirectiveStatus.conflicted.value
                    result[j].conflicts_with_index = i
    return result


def merge_capability_flags(reconciled: list[Reconciled]) -> dict[str, Any]:
    flags: dict[str, Any] = {}
    for r in reconciled:
        if r.status != DirectiveStatus.active.value:
            continue
        for key, value in (r.directive.capability_flags or {}).items():
            if isinstance(value, bool) and key in flags and isinstance(flags[key], bool):
                flags[key] = flags[key] and value  # a single False disables it
            else:
                flags[key] = value
    return flags


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
