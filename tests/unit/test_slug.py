"""Unit tests for slug utilities."""

from __future__ import annotations

from app.utils.slug import slugify, unique_slug


def test_slugify_basic():
    assert slugify("Acme Co.") == "acme-co"
    assert slugify("Northwind Labs!!") == "northwind-labs"


def test_slugify_empty_uses_fallback():
    assert slugify("", fallback="client") == "client"
    assert slugify("!!!", fallback="x") == "x"


def test_unique_slug_returns_base_when_free():
    assert unique_slug("acme", exists=lambda s: False) == "acme"


def test_unique_slug_appends_counter_when_taken():
    taken = {"acme", "acme-2"}
    assert unique_slug("acme", exists=lambda s: s in taken) == "acme-3"
