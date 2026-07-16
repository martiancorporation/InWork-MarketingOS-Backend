---
name: write-tests
description: >-
  Writes tests for this FastAPI backend the repo's way — hermetic pytest with the
  conftest fixtures (client, admin_headers, make_user), SQLite-backed, faking
  external services, and mandatory authorization (403/404) and validation (422)
  coverage. Use when adding or updating tests, or when new code needs test
  coverage.
allowed-tools: Read, Grep, Glob, Edit, Write, Bash
---

# Write tests

Match the existing suite in `tests/`. Tests are **hermetic**: in-memory SQLite, no network,
external services faked. See `tests/conftest.py`.

## Layout
- `tests/unit/` — pure logic in isolation (security, web/SSRF guard, rate limiter, parsers).
- `tests/integration/` — routes end-to-end via the FastAPI `TestClient`, one file per feature
  (`test_<feature>.py`).

## Fixtures (from `tests/conftest.py`)
- `client` — `TestClient` with `get_db` overridden to a per-test SQLite session.
- `db_session` — the raw session (seed rows directly when needed).
- `admin_headers` — provisions an admin and returns `{"Authorization": "Bearer …"}`.
- `make_user(email=…, role=…)` — factory returning `(user_json, that_user's_headers)`; use it
  to test non-admin / cross-user access.

## Integration test recipe
For a client-scoped feature, cover the full slice:
1. **Create** → assert `201` and the returned shape.
2. **List** → assert the pagination envelope: `items`, `total`, `page`, `page_size`.
3. **Get / update / delete** → assert status + effect.
4. **Authorization (required):** a user NOT assigned to the client must get **404** (not 403)
   on get/list/update — proves the anti-IDOR scoping. Use `make_user` + assignments.
5. **Validation:** a bad body (missing field, over-long text, unknown field) returns **422**.

```python
def test_list_is_paginated(client, admin_headers):
    # ... create a couple of rows ...
    r = client.get(f"{API}/clients/{cid}/things?page=1&page_size=1", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"items", "total", "page", "page_size"}
    assert len(body["items"]) == 1 and body["total"] >= 2

def test_non_member_gets_404(client, admin_headers, make_user):
    _, user_headers = make_user(email="outsider@test.com")
    r = client.get(f"{API}/clients/{cid}/things", headers=user_headers)
    assert r.status_code == 404  # not 403 — existence must not leak
```

## Faking external services
- **Anthropic:** the suite runs with `ANTHROPIC_API_KEY=""` so AI takes its deterministic
  fallback. To test the configured path, monkeypatch the client's methods.
- **S3:** override the `get_storage` dependency with a fake (see `tests/unit/test_upload_service.py`).
- **Embeddings:** `INTEL_EMBEDDING_PROVIDER=fake` (local deterministic embedder).
- Keep new global side-effects behind an env flag so the suite stays hermetic.

## Postgres-only paths
SQLite can't exercise pgvector, native enums, `FOR UPDATE SKIP LOCKED`, or advisory locks —
those are code-guarded (`dialect == "postgresql"`). Don't assert Postgres-specific behavior in
unit tests; note it's covered by the CI migrations job instead.

## Run
```
.venv/bin/python -m pytest tests/integration/test_<feature>.py -q
.venv/bin/python -m pytest -q          # whole suite before finishing
```
All green, no skips left behind.
