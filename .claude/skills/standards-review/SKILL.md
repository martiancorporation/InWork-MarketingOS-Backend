---
name: standards-review
description: >-
  Reviews the current changes in this FastAPI backend against the repo's house
  rules — layering, transaction ownership, object-level authz (404 not 403),
  bounded inputs/outputs, typed errors, no secrets, async safety. Use when asked
  to review code, check a diff, self-check before a PR, or audit for standards
  compliance.
allowed-tools: Read, Grep, Glob, Bash
---

# Standards review

Review the working changes against this repo's contracts. This is a *standards* review
(complements the built-in `code-review` skill, which hunts for general bugs).

## 1. Scope the diff
```
git status --short
git diff            # unstaged
git diff --staged   # staged
```
Review only what changed, plus the files it touches.

## 2. Checklist — flag any violation with file:line, why, and the fix

**Layering**
- [ ] Routers stay thin — no business logic, no raw queries, no `.commit()`.
- [ ] Business logic lives in a service; data access lives in a repository.
- [ ] No layer reaches "around" the next (e.g. router → repository directly).

**Transactions**
- [ ] Repositories never call `commit()` (grep the diff). Only services commit.
- [ ] Multi-step writes are atomic (one commit, rollback on failure → typed error).

**Authorization (anti-IDOR)**
- [ ] Every client-scoped route calls `ClientService(db).get_client(user, client_id)` first.
- [ ] Inaccessible resources return **404, never 403** (so ids can't be probed).
- [ ] Admin-only routes use the `AdminUser` dependency.

**Bounded inputs**
- [ ] Request schemas inherit `StrictModel` (`extra="forbid"`).
- [ ] Free-text fields have `max_length`; lists/batches are capped; emails use `EmailStr`.

**Bounded outputs**
- [ ] List endpoints take the `Pagination` dependency and return `{items,total,page,page_size}`.
- [ ] The repository pushes `LIMIT`/`OFFSET` + a `func.count()` — no load-all-then-slice on
      large tables.

**Errors**
- [ ] Only typed exceptions from `app/core/exceptions.py`; no `HTTPException`/`JSONResponse`
      in services.
- [ ] No silent `except Exception` that swallows a real error without a stated reason.

**Secrets & config**
- [ ] No new `os.environ` reads outside `app/core/config`; new settings added there and to
      `.env.example`.
- [ ] No secrets, keys, or real connection strings in code or committed `.env*`.

**Async safety**
- [ ] No blocking I/O (S3, sync DB, requests) directly inside an `async def` handler —
      offloaded via `anyio.to_thread.run_sync`.

**Prompts & tests**
- [ ] LLM prompts live under `app/prompts/<feature>/`, not inlined.
- [ ] New behavior has tests, including an authorization (403/404) case.

## 3. Fast greps
```
git diff | grep -nE '\.commit\(' ; echo "^ commits — must be in a service, not a repository"
git diff | grep -nE 'os\.environ'  ; echo "^ env reads — must be in app/core/config only"
git diff | grep -nE 'HTTPException|JSONResponse' ; echo "^ must not appear in services"
```

## 4. Report
List findings ordered by severity (correctness/security first), each with **file:line →
why → concrete fix**. If everything passes, say so explicitly and note what you checked.
Then remind the reviewer to run the `verify-changes` gate.
