"""Regression: audit before/after diff survives the sync-route threadpool hop.

A sync FastAPI route runs in a threadpool whose context is a *copy* of the
request's. The old code rebound a ContextVar inside the route, which was lost on
the way back to the async audit middleware, so ``changes`` recorded ``null``. The
fix seeds a mutable holder and mutates it (shared by reference across the copy).
These tests reproduce the copied-context scenario — the old rebind approach fails
them; the holder approach passes.
"""

from __future__ import annotations

import contextvars

from app.core.request_context import (
    begin_audit_changes,
    get_audit_changes,
    reset_audit_changes,
    set_audit_changes,
)


def test_begin_seeds_empty():
    token = begin_audit_changes()
    try:
        assert get_audit_changes() is None
    finally:
        reset_audit_changes(token)


def test_set_then_get_same_context():
    token = begin_audit_changes()
    try:
        diff = {"name": {"before": "A", "after": "B"}}
        set_audit_changes(diff)
        assert get_audit_changes() == diff
    finally:
        reset_audit_changes(token)


def test_change_set_in_copied_context_is_visible_in_original():
    # Seed the holder, then set the diff inside a COPIED context (what a
    # threadpool worker runs in). The mutation must be visible back here.
    token = begin_audit_changes()
    try:
        diff = {"status": {"before": "active", "after": "paused"}}
        ctx = contextvars.copy_context()
        ctx.run(set_audit_changes, diff)
        assert get_audit_changes() == diff  # would be None with the old rebind
    finally:
        reset_audit_changes(token)


def test_set_without_begin_lazily_creates_holder():
    # Non-request contexts (unit tests / scripts) can set without a prior begin.
    diff = {"industry": {"before": "x", "after": "y"}}
    set_audit_changes(diff)
    assert get_audit_changes() == diff
