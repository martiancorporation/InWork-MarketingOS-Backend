# InWork MarketingOS — Backend API Reference

> **Audience:** Web development team (frontend integration).
> **Purpose:** Complete, implementation-ready reference for every backend API, organized by application flow, module, screen, and user role. Reflects the decisions taken in the project meetings (real per-client OAuth for Meta / Google Ads, CSV analytics import, field-level audit trail, manual "add to source" PII stance, additive compliance register, goal-relative campaign health, KPI watchdog, AI brand extraction).

---

## Table of contents

1. [How to read this document](#1-how-to-read-this-document)
2. [Global conventions](#2-global-conventions)
   - Base URL & versioning · Authentication · Roles & access model · Error envelope · Status codes · Pagination · Rate limiting
3. [Application flow → screen map](#3-application-flow--screen-map)
4. [Module 1 — Authentication](#module-1--authentication) *(Login screen)*
5. [Module 2 — Users & Access](#module-2--users--access-admin) *(Agency admin)*
6. [Module 3 — Clients](#module-3--clients) *(Agency: Clients list & detail)*
7. [Module 4 — Client Onboarding Wizard](#module-4--client-onboarding-wizard) *(Onboard Client, 8 steps)*
8. [Module 5 — Client Assignments](#module-5--client-assignments-admin) *(Agency admin)*
9. [Module 6 — Client Dashboard (AI)](#module-6--client-dashboard-ai) *(Client Dashboard)*
10. [Module 7 — Client Intelligence](#module-7--client-intelligence) *(Project AI / admin debug)*
11. [Module 8 — Ask AI (Project Assistant)](#module-8--ask-ai-project-assistant) *(Project-AI / Ask-AI chat)*
12. [Module 9 — Analytics](#module-9--analytics) *(Analytics screen)*
13. [Module 10 — Campaigns](#module-10--campaigns) *(Campaigns / Dashboard)*
14. [Module 11 — Alerts (Watchdog)](#module-11--alerts-watchdog) *(Dashboard alerts panel)*
15. [Module 12 — Marketing Calendar](#module-12--marketing-calendar) *(Content Calendar)*
16. [Module 13 — Conversations (Shared Inbox)](#module-13--conversations-shared-inbox) *(Conversations)*
17. [Module 14 — Compliance](#module-14--compliance) *(Compliance Gate)*
18. [Module 15 — Plan (Kanban)](#module-15--plan-kanban) *(Plan screen)*
19. [Module 16 — Reports](#module-16--reports) *(Reports screen)*
20. [Module 17 — Integrations](#module-17--integrations) *(Integration screen)*
21. [Module 18 — Uploads](#module-18--uploads) *(Global file service)*
22. [Module 19 — Notifications](#module-19--notifications) *(Notification Center)*
23. [Module 20 — Automation / Platform Ops](#module-20--automation--platform-ops-admin) *(Admin / scheduler)*
24. [Module 21 — AI Usage](#module-21--ai-usage-admin) *(Token Usage screen)*
25. [Module 22 — Audit Log](#module-22--audit-log-admin) *(Audit Logs screen)*
26. [Appendix A — Enum reference](#appendix-a--enum-reference)

---

## 1. How to read this document

The application has two areas, matching the frontend:

- **Agency area (the "Main Dashboard")** — where the agency operates across all clients: the client roster, onboarding, user management, assignments, and the admin observability screens (Users & Access, Audit Logs, Token Usage).
- **Client area** — everything scoped to a single client, opened from the client shell: Dashboard, Analytics, Content Calendar, Conversations, Compliance, Plan, Reports, Integration, Project-AI.

Every endpoint entry lists: **method + path**, **auth/permissions**, **rate limiting**, **request payload**, **query params**, **success response + status**, **error responses + status**, **object scoping**, and **why/when it is called**. The **screen/module** and **frontend integration notes** are given once at the top of each module (they apply to all its endpoints).

---

## 2. Global conventions

### Base URL & versioning
- All endpoints are under **`/api/v1`** (configurable via `API_V1_PREFIX`).
- Interactive docs (Swagger UI): **`/docs`** · OpenAPI schema: **`/openapi.json`**.
- Liveness (unversioned): **`GET /health`**.

### Authentication
- **Scheme:** JWT bearer (HS256). Obtain a token from `POST /api/v1/auth/login`.
- **Header on every protected call:** `Authorization: Bearer <access_token>`.
- **Expiry:** access tokens expire after **30 minutes** by default (`ACCESS_TOKEN_EXPIRE_MINUTES`); the login response returns `expires_in` (seconds). There is **no refresh token / server-side logout** yet — the token is valid until it expires; the frontend should re-login on `401`.
- **No public sign-up.** All users are provisioned by an admin (Module 2).

### Roles & access model
- **Roles:** `admin`, `manager`, `user`.
- **`admin`** — sees and does everything across all clients; only role allowed on admin-only endpoints (user management, onboarding, assignments, intelligence rebuild, automation, AI-usage, audit).
- **`manager` / `user`** (non-admin) — can only see **clients assigned to them** (via Module 5). Any client they are not assigned to is reported as **`404 Not Found`, never `403`** — so client IDs can't be probed. This "object-level authorization" applies to every `/clients/{client_id}/...` route.
- **Uploads** are **owner-scoped** the same way (a non-admin sees only their own uploads).
- **Notifications** are **per-user** (each caller sees only their own).

### Error envelope
Every error returns the same JSON shape, plus an `X-Request-ID` response header for log correlation:
```json
{ "error": { "code": "not_found", "message": "Client not found.", "details": null } }
```

### Status codes
| Code | `error.code` | Meaning / when |
| --- | --- | --- |
| `200 OK` | — | Success (read, update, action). |
| `201 Created` | — | Resource created (login is 200, not 201). |
| `204 No Content` | — | Success with empty body (assignment delete). |
| `400 Bad Request` | `bad_request` | Malformed input the schema can't express (e.g. empty file). |
| `401 Unauthorized` | `unauthorized` | Missing/invalid/expired token. |
| `403 Forbidden` | `forbidden` | Authenticated but not an admin on an admin-only route. |
| `404 Not Found` | `not_found` | Resource missing **or** not accessible to the caller. |
| `409 Conflict` | `conflict` | Duplicate (email/assignment) or illegal state transition. |
| `413 Content Too Large` | `payload_too_large` | Upload / CSV exceeds the size cap. |
| `415 Unsupported Media Type` | `unsupported_media_type` | Upload content-type not on the allow-list. |
| `422 Unprocessable Content` | `validation_error` | Body/query failed validation (missing field, bad enum, unknown field on strict models). |
| `429 Too Many Requests` | `too_many_requests` | Rate limit exceeded. |
| `503 Service Unavailable` | `service_unavailable` | External dependency unconfigured/failed (S3, OAuth provider token exchange). |

### Pagination
List endpoints accept `?page=` (integer ≥ 1, default `1`) and `?page_size=` (1–100, default `20`) and return:
```json
{ "items": [ ... ], "total": 123, "page": 1, "page_size": 20 }
```
A few list endpoints intentionally return the full set as `{ "items": [...], "total": N }` (no paging): recommendation decisions, integrations catalog, intelligence versions, automation digest. These are called out inline.

### Rate limiting
Only three endpoints are throttled (per worker; sliding window; disabled in dev/test):
| Endpoint | Limit |
| --- | --- |
| `POST /auth/login` | 10 requests / 60s |
| `POST /clients/onboarding/extract-brand` | 10 requests / 60s |
| `GET /clients/{id}/dashboard` | 30 requests / 60s |
| `POST /clients/{id}/assistant/chats/{chat_id}/messages` | 30 requests / 60s |

On exceed → `429`. The frontend should surface a friendly "please slow down" toast and back off.

---

## 3. Application flow → screen map

| # | Module | Area | Screen(s) | Primary role |
| --- | --- | --- | --- | --- |
| 1 | Authentication | — | Login | Public |
| 2 | Users & Access | Agency | Users & Access | Admin |
| 3 | Clients | Agency | Clients list, Client detail | All (scoped) |
| 4 | Onboarding Wizard | Agency | Onboard Client (8 steps) | Admin |
| 5 | Assignments | Agency | Users & Access / Client assignment | Admin |
| 6 | Dashboard (AI) | Client | Client Dashboard | All (scoped) |
| 7 | Intelligence | Client | Project-AI / Rules / Agent-Log (+ admin debug) | All (scoped) / Admin |
| 8 | Ask AI (Project Assistant) | Client | Project-AI (Ask AI) | All (scoped) |
| 9 | Analytics | Client | Analytics | All (scoped) |
| 10 | Campaigns | Client | Campaigns / Dashboard | All (scoped) |
| 11 | Alerts (Watchdog) | Client | Dashboard alerts panel | All (scoped) |
| 12 | Calendar | Client | Content Calendar | All (scoped) |
| 13 | Conversations | Client | Conversations (Shared Inbox) | All (scoped) |
| 14 | Compliance | Client | Compliance Gate | All (scoped) |
| 15 | Plan | Client | Plan (Kanban) | All (scoped) |
| 16 | Reports | Client | Reports | All (scoped) |
| 17 | Integrations | Client | Integration | All (scoped) |
| 18 | Uploads | Global | (used by onboarding docs, reports, attachments) | All (owner-scoped) |
| 19 | Notifications | Global | Notification Center | All (per-user) |
| 20 | Automation | Agency | (scheduler; admin manual trigger) | Admin |
| 21 | AI Usage | Agency | Token Usage | Admin |
| 22 | Audit Log | Agency | Audit Logs | Admin |

**Typical flow:** Login → (agency) see Clients → Onboard a client (8-step wizard, with AI brand extraction + consistency check) → Assign the client to a manager/user → open the client → Dashboard (AI health/brief/watchdog/recommendations) → work across Analytics, Calendar, Conversations, Compliance, Plan, Reports, Integrations. Admins additionally use Automation, Token Usage, Audit Logs.

---

## Module 1 — Authentication
**Screen:** Login. **Role:** Public. **Frontend notes:** On success, store `access_token` and set the `Authorization: Bearer` header on the shared HTTP client; persist the returned `user` for the role switch / nav gating. On any `401` from a later call, clear the token and route back to Login. Login is the only auth endpoint — there is no signup/refresh/logout API.

### `POST /api/v1/auth/login`
- **Auth:** Public.
- **Rate limited:** Yes — 10 / 60s.
- **Request payload:** `LoginRequest` (JSON)
  - `email` — `EmailStr` — **required**
  - `password` — `str` — **required**, `min_length=1`
- **Query params:** None.
- **Success `200`:** `TokenResponse`
  - `access_token` — `str` (JWT)
  - `token_type` — `str` (default `"bearer"`)
  - `expires_in` — `int` (seconds to expiry)
  - `user` — `UserRead` (`id`, `email`, `name`, `role`, `is_active`, `created_at`)
- **Errors:** `401` invalid credentials (generic — same message for unknown email vs wrong password, no user enumeration); `422` bad email / empty password; `429` rate limited.
- **Object scoping:** N/A.
- **Why/when:** The single sign-in entry point; exchanges email + password for a bearer token.

---

## Module 2 — Users & Access (Admin)
**Screen:** Users & Access (agency admin). **Role:** Admin only. **Frontend notes:** Show this screen and the "Create user" button **only to admins**. Roles offered: `admin`, `manager`, `user`. `PATCH` is used both to edit a profile/role and to deactivate (`is_active=false`) — there is no hard-delete of users. Password must be ≥ 8 chars with at least one letter and one digit.

### `POST /api/v1/users`
- **Auth:** Admin only.
- **Rate limited:** No.
- **Request payload:** `UserCreate` (JSON)
  - `name` — `str` — **required**, `min_length=1`, `max_length=120`
  - `email` — `EmailStr` — **required**
  - `password` — `str` — **required**, `min_length=8`, `max_length=128`, must contain ≥1 letter and ≥1 digit
  - `role` — `UserRole` — optional, default `user` (`admin`/`manager`/`user`)
- **Success `201`:** `UserRead`.
- **Errors:** `401`; `403` non-admin; `409` email already exists; `422` validation / weak password.
- **Why/when:** Admin provisions a new user (no self-signup).

### `GET /api/v1/users`
- **Auth:** Admin only. **Rate limited:** No.
- **Query params:** `page`, `page_size`.
- **Success `200`:** `UserListResponse` = `{ items: UserRead[], total, page, page_size }`.
- **Errors:** `401`; `403`; `422`.
- **Why/when:** List all users (paginated) on the Users & Access screen.

### `PATCH /api/v1/users/{user_id}`
- **Auth:** Admin only. **Rate limited:** No.
- **Request payload:** `UserUpdate` (partial — only present fields applied)
  - `name` — `str | None`, `min_length=1`, `max_length=120`
  - `role` — `UserRole | None`
  - `is_active` — `bool | None`
- **Success `200`:** `UserRead`.
- **Errors:** `401`; `403`; `404` user not found; `422`.
- **Why/when:** Change a user's name/role or (de)activate them.

---

## Module 3 — Clients
**Screen:** Clients list (agency) + Client detail (opens the client shell). **Role:** List/detail available to all authenticated users (scoped); create/update is admin-only (see Modules 4 & below). **Frontend notes:** The list is the agency home grid — supports `search` (name/industry) and a `status` filter. For non-admins the list already returns only their assigned clients (no 404 on the list itself). The detail endpoint returns the full nested profile that powers the client shell header, brand chips, platforms, and contacts.

### `GET /api/v1/clients`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`, `search` (`str`, matches name/industry), `status` (`ClientStatus`: `draft`/`active`/`inactive`/`paused`/`onboarding`/`archived`).
- **Success `200`:** `ClientListResponse` = `{ items: ClientListItem[], total, page, page_size }`. `ClientListItem`: `id`, `slug`, `name`, `business_type`, `industry`, `website`, `location`, `status`, `onboarding_step`, `spend`, `leads`, `cpl`, `created_at`, `onboarding_percent`, `onboarding_completed`.
- **Errors:** `401`; `422`.
- **Object scoping:** Admin sees all; non-admin sees only assigned clients (filtered result set).
- **Why/when:** Render the agency client roster.

### `GET /api/v1/clients/{client_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `ClientRead` — full profile: identity (`id`, `slug`, `name`, `business_type`, `industry`, `website`, `location`, `language`, `timezone`, `markets`), brand (`about_brand`, `brand_voice`, `brand_extracted`, `color_guidelines`, `logo_url`, `brand_colors[]`, `brand_fonts[]`), `goals`, `status`, `pipeline_stage`, `onboarding_step`, `platforms[]`, `contacts[]`, `created_at`, plus computed `onboarding_percent`, `onboarding_completed`.
- **Errors:** `401`; `404` not found / not accessible.
- **Object scoping:** Admin sees all; non-admin only assigned; inaccessible → `404` (never 403).
- **Why/when:** Load a single client's full profile for the client shell.

### `PATCH /api/v1/clients/{client_id}`
- **Auth:** **Admin only.** **Rate limited:** No.
- **Request payload:** `ClientUpdate` (partial)
  - `name` — `str | None`, `min_length=1`, `max_length=200`
  - `business_type` — `str | None`
  - `industry` — `str | None`
  - `website` — `str | None`
  - `location` — `str | None`
  - `status` — `ClientStatus | None`
- **Success `200`:** `ClientRead`.
- **Errors:** `401`; `403`; `404`; `422`.
- **Note:** This edit records a **field-level before/after diff** in the audit log (see Module 22).
- **Why/when:** Admin changes a client's status or basic profile fields.

---

## Module 4 — Client Onboarding Wizard
**Screen:** Onboard Client — an **8-step wizard**: (1) Basics, (2) Brand, (3) Platforms, (4) Goals, (5) Compliance, (6) Contacts, (7) Documents, (8) Review. **Role:** Admin only (except the brand-extraction helper, which any authenticated user may call). **Frontend notes:** Two supported patterns — (a) **atomic** submit the whole wizard at once via `POST /clients/onboarding`; or (b) **progressive** autosave: create the draft in step 1 (`/onboarding/draft`), then `PATCH .../onboarding` after each subsequent step (send only that step's section — partial saves never wipe earlier steps), attach documents in step 7, run the AI consistency check on the Review step, then `complete`. Use the brand-extraction helper in step 2 to prefill colors/fonts/tone from the client's website. All step responses return recomputed `readiness` and `onboarding` progress so the wizard can show a live completeness meter.

### `POST /api/v1/clients/onboarding`  *(atomic — full wizard in one call)*
- **Auth:** Admin only. **Rate limited:** No.
- **Request payload:** `OnboardingRequest` (**strict** — unknown fields rejected)
  - `name` — `str` — **required**, `1..160`
  - `business_type` — `str` — **required**, `1..120`
  - `industry` — `str` — **required**, `1..120`
  - `website` — `str | None`, `≤255`
  - `language` — `str | None`, `≤60`
  - `location` — `str | None`, `≤160`
  - `markets` — `str | None`, `≤2000`
  - `brand` — `BrandIn` — **required**: `brand_voice` (**required**, `1..2000`), `about_brand` (`≤20000`), `brand_extracted` (`≤20000`), `color_guidelines` (`≤20000`), `logo_url` (`≤1024`), `colors` (`list[BrandColorIn]` ≤24; each `hex` matching `#RGB`/`#RGBA`/`#RRGGBB`/`#RRGGBBAA` + optional `label` ≤60), `fonts` (`list[str]` ≤12)
  - `platforms` — `list[str]` — **required**, `1..32` items (deduped/lowercased; must yield ≥1)
  - `goals` — `str | None`, `≤20000`
  - `compliance` — `ComplianceIn` (optional): `feed` (`str | None`, `≤20000`)
  - `client_contacts` — `list[ContactIn]` (optional) — **at least one must have an email**; `ContactIn`: `name` (**required**, `1..120`), `role`/`department` (`≤120`), `email` (`EmailStr | None`), `phone` (`≤40`), `description` (`≤2000`)
  - `inwork_contacts` — `list[ContactIn]` (optional)
  - `documents` — `list[DocumentRef]` (optional): `name` (`1..255`), `kind` (`DocumentKind` default `other`: `brand`/`compliance`/`goals`/`contract`/`brief`/`creative`/`other`), `size_bytes` (`≥0`), `mime_type` (`≤120`), `storage_url` (`1..1024`)
- **Success `201`:** `OnboardingResponse` = `{ client: ClientRead, readiness: ReadinessReport, intelligence: IntelligenceStatus | null }`. `ReadinessReport`: `score` (0–100), `completed[]`, `missing[]`.
- **Errors:** `401`; `403`; `422` (missing required, no platform, no contact-with-email, unknown field).
- **Why/when:** Create a client in one shot when the whole wizard is filled before submit.

### `POST /api/v1/clients/onboarding/draft`  *(step 1 — Basics)*
- **Auth:** Admin only. **Rate limited:** No.
- **Request payload:** `OnboardingDraftRequest` (strict): `name` (**required**, `1..160`), `business_type` (**required**, `1..120`), `industry` (**required**, `1..120`), `website` (`≤255`), `language` (`≤60`), `location` (`≤160`), `markets` (`≤2000`).
- **Success `201`:** `OnboardingStepResponse` = `{ client, readiness, onboarding: { step, total_steps: 8, percent, completed }, intelligence }`.
- **Errors:** `401`; `403`; `422`.
- **Why/when:** Mandatory step 1 — creates the draft client whose `id` backs every later autosave.

### `PATCH /api/v1/clients/{client_id}/onboarding`  *(steps 2–6 autosave)*
- **Auth:** Admin only. **Rate limited:** No.
- **Request payload:** `OnboardingStepUpdate` (**strict**, partial — only sections present are written)
  - `step` — `int | None`, `1..8` (advances the progress meter monotonically)
  - `basics` — `BasicsUpdate | None` (all sub-fields optional; same bounds as draft)
  - `brand` — `BrandUpdate | None` (all optional; `brand_voice` **not** required here)
  - `platforms` — `list[str] | None`, `≤32`
  - `goals` — `str | None`, `≤20000`
  - `compliance` — `ComplianceIn | None`
  - `client_contacts` — `list[ContactIn] | None`, `≤100`
  - `inwork_contacts` — `list[ContactIn] | None`, `≤100`
- **Success `200`:** `OnboardingStepResponse`.
- **Errors:** `401`; `403`; `404`; `422` (unknown field, out-of-range step).
- **Why/when:** Autosave one step; saving step 4 never clears step 2.

### `POST /api/v1/clients/{client_id}/documents`  *(step 7 — Documents)*
- **Auth:** Admin only. **Rate limited:** No.
- **Request payload:** `DocumentsRequest` (strict): `documents` — `list[DocumentRef]` **required**, `1..100`. *(References to already-uploaded files — the file bytes are uploaded via Module 18 first.)*
- **Success `201`:** `OnboardingStepResponse`.
- **Errors:** `401`; `403`; `404`; `422`.
- **Why/when:** Attach uploaded document references to the client.

### `POST /api/v1/clients/{client_id}/onboarding/consistency`  *(step 8 — Review, AI check)*
- **Auth:** Admin only. **Rate limited:** No.
- **Request payload:** None.
- **Success `200`:** `ConsistencyReport` = `{ findings: [{ level: ok|warn|error, message, step? }], has_blocking: bool, ai_generated: bool }`.
- **Errors:** `401`; `403`; `404`.
- **Why/when:** AI cross-field consistency check — flags contradictions (e.g. "industry: steel" vs brand copy about gardens) before going live.

### `POST /api/v1/clients/{client_id}/onboarding/complete`  *(step 8 — finalize)*
- **Auth:** Admin only. **Rate limited:** No.
- **Request payload:** None.
- **Success `200`:** `OnboardingStepResponse`.
- **Errors:** `401`; `403`; `404`; `422` if finalization preconditions unmet.
- **Why/when:** Finalize onboarding — transitions the draft into a live client.

### `POST /api/v1/clients/onboarding/extract-brand`  *(step 2 helper — AI)*
- **Auth:** Authenticated (any role). **Rate limited:** **Yes — 10 / 60s.**
- **Request payload:** `BrandExtractionRequest` (strict): `website` — `str` **required**, `1..255`.
- **Success `200`:** `BrandExtraction` = `{ summary, colors: str[], fonts: str[], tone?, imagery?, ai_generated: bool }` (`ai_generated=false` when the deterministic dev fallback answered).
- **Errors:** `401`; `422`; `429`.
- **Note:** Fetch is SSRF-guarded and uses (when configured) a ScrapingBee proxied render → headless Chromium → httpx fallback; optional Brave web research enriches the summary.
- **Why/when:** Prefill the Brand step from the client's website — colors/fonts/tone/imagery/summary.

---

## Module 5 — Client Assignments (Admin)
**Screen:** Users & Access / the client's "assign" control (agency admin). **Role:** Admin only. **Frontend notes:** This is the mechanism that grants a non-admin access to a client — assigning a client to a user is what makes it appear in that user's client list. Show assign/unassign controls only to admins.

Base path: `/clients/{client_id}/assignments`.

### `GET /api/v1/clients/{client_id}/assignments`
- **Auth:** Admin only. **Rate limited:** No.
- **Success `200`:** `AssignmentListResponse` = `{ items: AssignmentRead[], total }`. `AssignmentRead`: `client_id`, `assigned_by` (`uuid|null`), `created_at`, `user: UserRead`.
- **Errors:** `401`; `403`; `404` client not found.
- **Why/when:** List the users a client is assigned to.

### `POST /api/v1/clients/{client_id}/assignments`
- **Auth:** Admin only. **Rate limited:** No.
- **Request payload:** `AssignmentCreate`: `user_id` — `uuid.UUID` **required**.
- **Success `201`:** `AssignmentRead` (`assigned_by` = acting admin).
- **Errors:** `401`; `403`; `404` client or target user; `409` already assigned; `422`.
- **Why/when:** Grant a non-admin access to a client.

### `DELETE /api/v1/clients/{client_id}/assignments/{user_id}`
- **Auth:** Admin only. **Rate limited:** No.
- **Success `204`:** empty body.
- **Errors:** `401`; `403`; `404` assignment/client not found.
- **Why/when:** Revoke a non-admin's access to a client.

---

## Module 6 — Client Dashboard (AI)
**Screen:** Client Dashboard. **Role:** All authenticated (scoped). **Frontend notes:** The dashboard is a **single call** that returns everything (health score, executive brief, watchdog, recommendations). It is **rate limited (30/60s)** and is a paid-AI route — cache the response, don't refetch on every re-render, and show a graceful state if `ai_generated=false` (Claude unconfigured → deterministic fallback). Recommendation cards use the accept/modify/reject decision endpoint; the decisions history endpoint backs an "activity" view.

Base path: `/clients/{client_id}`.

### `GET /api/v1/clients/{client_id}/dashboard`
- **Auth:** Authenticated (any role). **Rate limited:** **Yes — 30 / 60s.**
- **Request payload:** None.
- **Success `200`:** `DashboardResponse`:
  - `health_score` — `{ score: 0–100, band: excellent|good|attention|critical, drivers: [{ label, delta }] }`
  - `executive_brief` — `{ headline, metrics: [{ label, value, delta, tone: up|down|flat }], budget: { spent, total, pace: on-track|ahead|behind }, top_campaign: { name, note }, worst_campaign: { name, note }, pending_actions: str[] }`
  - `watchdog` — `[{ id, kind: alert|opportunity, title, detail, severity: low|medium|high }]`
  - `recommendations` — `[{ id (rec_key), title, category: budget|creative|audience|compliance|growth, severity, summary, reason, confidence: 0–100, expected_impact, decision: RecommendationDecisionRead|null }]`
  - `ai_generated` — `bool`
- **Errors:** `401`; `404` client inaccessible; `429`.
- **Object scoping:** Admin all; non-admin only assigned; inaccessible → `404`.
- **Why/when:** Render the full client dashboard in one request.

### `POST /api/v1/clients/{client_id}/recommendations/{rec_key}/decision`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `RecommendationDecisionRequest`: `decision` — `RecommendationDecision` **required** (`accepted`/`modified`/`rejected`); `reason` — `str | None`, `≤2000`.
- **Path param:** `rec_key` (`str`, ≤80 — stable recommendation id).
- **Success `201`:** `RecommendationActionRead` = `{ id, rec_key, decision, reason?, decided_by?, created_at }`.
- **Errors:** `401`; `404` client inaccessible; `422`.
- **Why/when:** Record a human accept/modify/reject on a recommendation card.

### `GET /api/v1/clients/{client_id}/recommendations/decisions`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** None *(returns full list, not paginated)*.
- **Success `200`:** `RecommendationActionListResponse` = `{ items: RecommendationActionRead[], total }`.
- **Errors:** `401`; `404`.
- **Why/when:** Show the decision history / audit trail for recommendations.

---

## Module 7 — Client Intelligence
**Screen:** Project-AI / Rules-configuration / Agent-Log (client), plus an **admin-only debug** context view. **Role:** Reads are available to any assigned user; rebuild / directive-resolve / context are **admin only**. **Frontend notes:** The intelligence profile is **built asynchronously** — after onboarding or a compliance change, poll `/intelligence/status` until `status` is `ready` (or `failed`) and show a "building your AI profile…" state. Use `/intelligence` to render the profile summary + active directives (the client's "rules"). Version endpoints back a history view. The admin `context` endpoint is pure debug (exposes the raw agent preamble + retrieved RAG chunks) — keep it behind an admin/dev-only surface.

Base path: `/clients` (+ `/{client_id}/...`).

### `GET /api/v1/clients/{client_id}/intelligence`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `IntelligenceResponse` = `{ status: none|building|ready|failed, version?, profile?: ClientProfileRead, directives: DirectiveRead[] }`. `ClientProfileRead`: `id`, `version`, `status`, `summary_md?`, `profile?`, `capability_flags?`, `created_at`. `DirectiveRead`: `id`, `type`, `category`, `text`, `tier`, `rank`, `confidence`, `status`, `capability_flags?`, `source_id?`, `conflicts_with_id?`.
- **Errors:** `401`; `404`.
- **Why/when:** Show the current AI profile + directives grounding the client's AI features.

### `GET /api/v1/clients/{client_id}/intelligence/status`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `IntelligenceStatus` = `{ status, version?, job_status?, updated_at? }`.
- **Errors:** `401`; `404`.
- **Why/when:** Lightweight poll of the async build state while a profile rebuilds.

### `GET /api/v1/clients/{client_id}/intelligence/versions`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `ProfileVersionItem[]` (bare array): `{ version, status, created_at }`.
- **Errors:** `401`; `404`.
- **Why/when:** Profile version history.

### `GET /api/v1/clients/{client_id}/intelligence/versions/{version}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `IntelligenceResponse` (for the requested version).
- **Errors:** `401`; `404` client inaccessible or version not found.
- **Why/when:** Inspect a specific historical version.

### `POST /api/v1/clients/{client_id}/intelligence/rebuild`
- **Auth:** **Admin only.** **Rate limited:** No.
- **Success `200`:** `IntelligenceStatus` (reflects the newly enqueued build).
- **Errors:** `401`; `403`; `404`.
- **Why/when:** Force a full rebuild (re-extract, re-embed, regenerate). Admin operational trigger.

### `POST /api/v1/clients/{client_id}/directives/{directive_id}/resolve`
- **Auth:** **Admin only.** **Rate limited:** No.
- **Query params:** `activate` (`bool`, default `true`) — keep active vs dismiss.
- **Success `200`:** `DirectiveRead`.
- **Errors:** `401`; `403`; `404` client or directive.
- **Why/when:** Resolve a conflicted directive (two opposing rules) by keeping or dismissing it.

### `GET /api/v1/clients/{client_id}/context`  *(admin-only debug)*
- **Auth:** **Admin only.** **Rate limited:** No.
- **Query params:** `query` (`str | None`) — optional RAG retrieval query.
- **Success `200`:** `ClientContextResponse` = `{ version?, preamble, capability_flags, directives: DirectiveRead[], retrieved: [{ text, source_label?, score }] }`.
- **Errors:** `401`; `403`; `404`.
- **Why/when:** Debug exactly what the AI agents receive for the client (preamble + directives + RAG chunks).

---

## Module 8 — Ask AI (Project Assistant)
**Screen:** Project-AI / "Ask AI about this project" (client). **Role:** All authenticated (scoped). **Frontend notes:** A per-client conversational assistant grounded in the client's intelligence profile + RAG knowledge (brand, goals, compliance, calendar, performance). Flow: create a chat, then POST messages to it; each reply comes back with the `sources` (retrieved knowledge snippets) it used. The ask endpoint is **rate-limited (30/60s)** and is a paid-AI route; it degrades to a deterministic, source-grounded reply when Claude is unconfigured (the reply still returns — never a 5xx). Chats are a **shared per-client surface** (any user with client access sees them; the creator is stamped internally). Backed by the `ai_chats` / `ai_chat_messages` tables.

Base path: `/clients/{client_id}/assistant`.

### `GET /api/v1/clients/{client_id}/assistant/chats`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`; `context_type` (`str`, ≤40, e.g. `project`).
- **Success `200`:** `AssistantChatListResponse` = `{ items: AssistantChatRead[], total, page, page_size }`. `AssistantChatRead`: `id`, `title?`, `context_type?`, `context_key?`, `created_at`, `updated_at`. Ordered most-recently-active first.
- **Errors:** `401`; `404` client inaccessible; `422`.
- **Object scoping:** admin all; non-admin only assigned; inaccessible → `404`.
- **Why/when:** List the client's project chats.

### `POST /api/v1/clients/{client_id}/assistant/chats`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `AssistantChatCreate` (strict): `title` (`str | None`, ≤200), `context_type` (`str | None`, ≤40, default `project`), `context_key` (`str | None`, ≤80).
- **Success `201`:** `AssistantChatRead`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Start a new chat thread.

### `GET /api/v1/clients/{client_id}/assistant/chats/{chat_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `AssistantChatDetail` = `AssistantChatRead` + `messages: AssistantMessageRead[]` (chronological). `AssistantMessageRead`: `id`, `role` (`user`/`assistant`/`system`), `content`, `tokens?`, `created_at`.
- **Errors:** `401`; `404` client or chat.
- **Why/when:** Open a chat with its full message history.

### `POST /api/v1/clients/{client_id}/assistant/chats/{chat_id}/messages`
- **Auth:** Authenticated (any role). **Rate limited:** **Yes — 30 / 60s** (paid-AI).
- **Request payload:** `AssistantAskRequest` (strict): `content` (`str`, **required**, `1..4000`).
- **Success `201`:** `AssistantAskResponse` = `{ message: AssistantMessageRead (the assistant reply), sources: str[] (retrieved knowledge snippets used to ground it) }`.
- **Errors:** `401`; `404` client or chat; `422`; `429`.
- **Why/when:** Ask the project AI a question — persists the user message + assistant reply and returns the reply plus its grounding sources.

### `DELETE /api/v1/clients/{client_id}/assistant/chats/{chat_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `MessageResponse` = `{ detail: "Chat deleted." }`.
- **Errors:** `401`; `404`.
- **Why/when:** Delete a chat thread.

---

## Module 9 — Analytics
**Screen:** Analytics (client). **Role:** All authenticated (scoped). **Frontend notes:** `/summary` powers the KPI cards and per-platform/daily charts (recharts); `/daily` backs a paginated raw table over a date range. Data lands either via `/ingest` (an integration or manual row entry) or the **`/import` CSV upload** (meeting decision — push reporting data via CSV until all live integrations exist). The CSV header must be `date,platform,impressions,clicks,conversions,leads,spend,revenue`; the endpoint returns per-row error messages so you can show a partial-success summary.

Base path: `/clients/{client_id}/analytics`.

### `POST /api/v1/clients/{client_id}/analytics/ingest`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `AnalyticsIngestRequest`: `rows` — `list[AnalyticsDailyIn]` **required**, `1..1000`. `AnalyticsDailyIn`: `date` (**required**), `platform` (`SocialPlatform` **required**), `impressions`/`clicks`/`conversions`/`leads` (`int ≥0`, default 0), `spend`/`revenue` (`float ≥0`, default 0).
- **Success `200`:** `AnalyticsIngestResponse` = `{ upserted: int }`.
- **Errors:** `401`; `404`; `422`.
- **Object scoping:** Admin all; non-admin only assigned; inaccessible → `404`.
- **Why/when:** Upsert daily rows (one per client/date/platform).

### `POST /api/v1/clients/{client_id}/analytics/import`  *(CSV upload)*
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `multipart/form-data`, field `file` — CSV, header `date,platform,impressions,clicks,conversions,leads,spend,revenue`. **Size cap 5 MB** (over → `413`).
- **Success `200`:** `AnalyticsCsvImportResponse` = `{ upserted: int, skipped: int, errors: str[] }`.
- **Errors:** `401`; `404`; `413`; `422`.
- **Why/when:** Bulk-import daily analytics from a CSV export.

### `GET /api/v1/clients/{client_id}/analytics/daily`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`; `start` (`date`, inclusive), `end` (`date`, inclusive), `platform` (`SocialPlatform`).
- **Success `200`:** `AnalyticsDailyListResponse` = `{ items: AnalyticsDailyRead[], total, page, page_size }`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Raw daily time series (paginated table).

### `GET /api/v1/clients/{client_id}/analytics/summary`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `start`, `end`, `platform` (no pagination).
- **Success `200`:** `AnalyticsSummary` = `{ totals: { impressions, clicks, conversions, leads, spend, revenue, ctr, cpl, roas }, by_platform: PlatformBreakdownRow[], daily: DailySeriesRow[] }`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Aggregated KPI rollup for the dashboard/analytics summary.

---

## Module 10 — Campaigns
**Screen:** Campaigns / Dashboard (client). **Role:** All authenticated (scoped). **Frontend notes:** Campaigns carry both **targets** (`target_cpl`/`target_ctr`/`target_conversion_rate`) and **actual rollup** counters; the health endpoint returns a **goal-relative** score (actual vs agreed targets — the project-level, cross-platform health concept from the meetings). `/compare` powers the A/B view and returns a per-metric `winners` map. `PATCH` autosaves either definition fields or the actual counters (from a manual edit or an integration push).

Base path: `/clients/{client_id}/campaigns`.

### `GET /api/v1/clients/{client_id}/campaigns`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`; `status` (`CampaignStatus`: `draft`/`active`/`paused`/`ended`).
- **Success `200`:** `CampaignListResponse` = `{ items: CampaignListItem[], total, page, page_size }` (each item includes raw counters + derived `ctr`, `cpl`, `conversion_rate`, `roas`).
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Campaigns overview grid.

### `POST /api/v1/clients/{client_id}/campaigns`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `CampaignCreate`: `name` (**required**, `1..200`), `objective` (`AdObjective`, default `awareness`), `status` (`CampaignStatus`, default `draft`), `start_date`/`end_date` (`date`; end ≥ start), `budget_usd` (`float ≥0`, default 0), `notes` (`≤20000`), `target_cpl`/`target_ctr`/`target_conversion_rate` (`float ≥0`).
- **Success `201`:** `CampaignRead` (definition + targets + counters + derived metrics + audit fields).
- **Errors:** `400`/`422` (incl. date order); `401`; `404`.
- **Why/when:** Create a campaign with KPI targets.

### `GET /api/v1/clients/{client_id}/campaigns/compare`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `ids` — `list[UUID]` **required**, ≥ 2.
- **Success `200`:** `CampaignCompareResponse` = `{ rows: CampaignCompareRow[], winners: { metric: campaign_id } }` (higher-is-better for ctr/conversion_rate/roas/leads; lower-is-better for cpl).
- **Errors:** `401`; `404` client or a campaign; `422` fewer than 2 ids.
- **Why/when:** A/B side-by-side comparison.

### `GET /api/v1/clients/{client_id}/campaigns/{campaign_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `CampaignRead`.
- **Errors:** `401`; `404`.
- **Why/when:** Campaign detail view.

### `GET /api/v1/clients/{client_id}/campaigns/{campaign_id}/health`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `CampaignHealth` = `{ campaign_id, score: 0–100, band: excellent|good|attention|critical, drivers: [{ label, delta }], summary, has_targets: bool, ai_generated: bool }`.
- **Errors:** `401`; `404`.
- **Why/when:** Goal-relative health widget on the campaign detail.

### `PATCH /api/v1/clients/{client_id}/campaigns/{campaign_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `CampaignUpdate` (partial): any of `name`, `objective`, `status`, `start_date`, `end_date`, `budget_usd`, `notes`, `target_*`, plus actual rollups `impressions`/`clicks`/`conversions`/`leads` (`int ≥0`), `spend`/`revenue` (`float ≥0`).
- **Success `200`:** `CampaignRead`.
- **Errors:** `400`/`422`; `401`; `404`.
- **Why/when:** Autosave campaign edits or push actuals.

### `DELETE /api/v1/clients/{client_id}/campaigns/{campaign_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `MessageResponse` = `{ detail: "Campaign deleted." }`.
- **Errors:** `401`; `404`.
- **Why/when:** Remove a campaign.

---

## Module 11 — Alerts (Watchdog)
**Screen:** Dashboard alerts panel (client). **Role:** All authenticated (scoped). **Frontend notes:** Alerts are produced by the **KPI watchdog** (breach of an agreed target → operator alert, with the offending metric/threshold/actual so the UI can explain *why*). `/evaluate` runs the watchdog on demand for one client; the platform-wide sweep is Module 20. The acknowledge → resolve flow gives each alert a human owner.

Base path: `/clients/{client_id}/alerts`.

### `GET /api/v1/clients/{client_id}/alerts`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`; `status` (`AlertStatus`: `open`/`acknowledged`/`resolved`), `severity` (`AlertSeverity`: `low`/`medium`/`high`), `kind` (`AlertKind`: `alert`/`opportunity`).
- **Success `200`:** `AlertListResponse` = `{ items: AlertRead[], total, page, page_size }`. `AlertRead`: `id`, `client_id`, `campaign_id`, `kind`, `severity`, `status`, `title`, `detail`, `metric`, `threshold`, `actual`, `rec_key`, `acknowledged_by`, `resolved_by`, `created_at`, `updated_at`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Alerts/opportunities panel.

### `POST /api/v1/clients/{client_id}/alerts/evaluate`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `AlertEvaluateResult` = `{ evaluated_campaigns, opened, updated, auto_resolved, alerts: AlertRead[] }`.
- **Errors:** `401`; `404`.
- **Why/when:** Run the watchdog over the client's campaigns now.

### `GET /api/v1/clients/{client_id}/alerts/{alert_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `AlertRead`.
- **Errors:** `401`; `404`.
- **Why/when:** Alert detail.

### `POST /api/v1/clients/{client_id}/alerts/{alert_id}/acknowledge`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `AlertRead` (status → `acknowledged`, `acknowledged_by` = current user).
- **Errors:** `401`; `404`; `409` illegal transition.
- **Why/when:** Operator claims ownership of an alert.

### `POST /api/v1/clients/{client_id}/alerts/{alert_id}/resolve`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `AlertRead` (status → `resolved`, `resolved_by` = current user).
- **Errors:** `401`; `404`; `409` illegal transition.
- **Why/when:** Mark an alert handled.

---

## Module 12 — Marketing Calendar
**Screen:** Content Calendar (client). **Role:** All authenticated (scoped). **Frontend notes:** `GET events` backs both the month grid and the "Drafts & Ideas" panel (filter by `year`+`month`, `stage`, `platform`, `type`, `approval_status`). Create/patch is the "New Post" drawer with nested `post` (caption/hashtags/CTA) and/or `ad` (budget/objective/audience) sub-objects; `PATCH` is a partial autosave. The `/approval` endpoint drives the client-approval workflow (approve / request changes / reject / re-submit for review) and appends to the event's activity log.

Base path: `/clients/{client_id}/calendar`.

### `GET /api/v1/clients/{client_id}/calendar/events`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`; `year` (`1970..9999`), `month` (`1..12`), `stage` (`EventStage`: `draft`/`scheduled`/`published`/`archived`), `platform` (`SocialPlatform`), `type` (`EventType`: `campaign`/`email`/`ad`/`review`/`content`/`meeting`), `approval_status` (`ApprovalStatus`: `approved`/`pending`/`changes_requested`/`rejected`).
- **Success `200`:** `EventListResponse` = `{ items: EventListItem[], total, page, page_size }`. `EventListItem`: `id`, `client_id`, `campaign_id?`, `title`, `type`, `platform`, `event_date`, `event_time`, `stage`, `approval_status`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Populate the month grid / drafts panel.

### `POST /api/v1/clients/{client_id}/calendar/events`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `EventCreate`: `title` (**required**, `1..200`), `type` (`EventType` **required**), `platform` (`SocialPlatform` **required**), `event_date` (**required**), `event_time` (**required**), `description`/`strategy` (`≤20000`), `stage` (`EventStage`, default `draft`), `campaign_id` (`UUID`), `post` (`EventPostIn`: `image_url ≤1024`, `caption ≤20000`, `hashtags ≤2000`, `cta_label ≤80`, `cta_url ≤1024`), `ad` (`EventAdIn`: `budget_usd ≥0`, `objective AdObjective`, `audience ≤20000`, `bid_strategy ≤60`, `duration_days ≥1`).
- **Success `201`:** `EventRead` (full detail incl. `post`, `ad`, `assets[]`, `activity[]`, approval fields, audit fields).
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Create a post or ad from the "New Post" drawer.

### `GET /api/v1/clients/{client_id}/calendar/events/{event_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `EventRead`.
- **Errors:** `401`; `404`.
- **Why/when:** Full event detail (day view).

### `PATCH /api/v1/clients/{client_id}/calendar/events/{event_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `EventUpdate` (partial — only present fields applied): any of `title`, `type`, `platform`, `event_date`, `event_time`, `description`, `strategy`, `stage`, `campaign_id`, `post`, `ad`.
- **Success `200`:** `EventRead`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Edit / reschedule (autosave).

### `POST /api/v1/clients/{client_id}/calendar/events/{event_id}/approval`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `ApprovalDecision`: `status` (`ApprovalStatus` **required**), `note` (`≤20000`).
- **Success `200`:** `EventRead` (approval fields updated; decision appended to activity).
- **Errors:** `401`; `404`; `422`; `409` illegal transition.
- **Why/when:** Client-approval workflow: approve / request changes / reject / set pending.

### `DELETE /api/v1/clients/{client_id}/calendar/events/{event_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `MessageResponse` = `{ detail: "Event deleted." }`.
- **Errors:** `401`; `404`.
- **Why/when:** Remove an event.

---

## Module 13 — Conversations (Shared Inbox)
**Screen:** Conversations (client). **Role:** All authenticated (scoped). **Frontend notes:** Thread list supports `folder`, `starred`, `category`, and `search` (subject/body). Compose creates a thread + first message; replies post messages into a thread. Message-level `PATCH` moves folder / (un)stars / relabels. The **"add to source"** action is the meeting's manual-PII stance — nothing from the inbox reaches the AI automatically; promoting a message explicitly feeds it into the client's knowledge/RAG layer.

Base path: `/clients/{client_id}/conversations`.

### `GET /api/v1/clients/{client_id}/conversations`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`; `folder` (`MessageFolder`: `inbox`/`sent`/`drafts`/`archive`/`spam`/`trash`), `starred` (`bool`), `category` (`str`), `search` (`str`).
- **Success `200`:** `ConversationListResponse` = `{ items: ConversationListItem[], total, page, page_size }`. `ConversationListItem`: `id`, `subject?`, `source`, `is_read`, `last_message_at?`, `message_count`, `preview`, `latest_folder?`, `latest_category?`, `latest_sender_email?`, `is_starred`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Inbox thread list.

### `POST /api/v1/clients/{client_id}/conversations`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `ConversationCreate`: `subject` (`≤255`), `body` (**required**, `1..20000`), `category` (`≤40`), `source` (`ConversationSource`: `email`/`sms`/`whatsapp`/`instagram`/`facebook`/`webform`/`internal`, default `email`), `folder` (`MessageFolder`, default `sent`), `recipients` (`list[RecipientIn]` ≤100; `email` `EmailStr` `≤255` **required**, `kind` `RecipientKind`: `to`/`cc`/`bcc`, default `to`).
- **Success `201`:** `ConversationRead` = `{ id, client_id, subject, source, is_read, last_message_at, created_at, messages: MessageRead[] }`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Compose a new thread (sender = current user).

### `GET /api/v1/clients/{client_id}/conversations/{conversation_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `ConversationRead` (with `messages[]`). `MessageRead`: `id`, `conversation_id`, `sender_user_id?`, `sender_email?`, `folder`, `category?`, `is_starred`, `body`, `created_at`, `added_to_source_at?`, `knowledge_source_id?`, `recipients[]`, `attachments[]`.
- **Errors:** `401`; `404`.
- **Why/when:** Open the full thread.

### `PATCH /api/v1/clients/{client_id}/conversations/{conversation_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `ConversationUpdate`: `is_read` (`bool`).
- **Success `200`:** `ConversationRead`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Mark a thread read/unread.

### `DELETE /api/v1/clients/{client_id}/conversations/{conversation_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `MessageResponse` = `{ detail: "Conversation deleted." }`.
- **Errors:** `401`; `404`.
- **Why/when:** Delete a thread.

### `POST /api/v1/clients/{client_id}/conversations/{conversation_id}/messages`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `MessageCreate`: `body` (**required**, `1..20000`), `folder` (`MessageFolder`, default `sent`), `category` (`≤40`), `recipients` (`list[RecipientIn]` ≤100).
- **Success `201`:** `MessageRead`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Reply in a thread (sender = current user).

### `POST /api/v1/clients/{client_id}/conversations/{conversation_id}/messages/{message_id}/add-to-source`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `MessageRead` (with `added_to_source_at`, `knowledge_source_id` set).
- **Errors:** `401`; `404`; `409` if already promoted.
- **Why/when:** Manually promote a message into the client's knowledge/RAG layer.

### `PATCH /api/v1/clients/{client_id}/conversations/{conversation_id}/messages/{message_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `MessageUpdate` (partial): `folder` (`MessageFolder`), `is_starred` (`bool`), `category` (`≤40`).
- **Success `200`:** `MessageRead`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Move to folder / (un)star / relabel a message.

---

## Module 14 — Compliance
**Screen:** Compliance Gate (client). **Role:** All authenticated (scoped). **Frontend notes:** The register is **additive** — entries are (de)activated, not deleted, so the effective ruleset has history. Any create/update/delete **enqueues an intelligence rebuild** (the effective rules feed the client's AI directives); `/sync` forces that rebuild immediately instead of waiting for the debounced enqueue. Filter by `kind` and `active_only`.

Base path: `/clients/{client_id}/compliance`.

### `GET /api/v1/clients/{client_id}/compliance`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`; `kind` (`ComplianceKind`: `brand_voice`/`banned`/`required`/`rule`/`note`), `active_only` (`bool`, default `false`).
- **Success `200`:** `ComplianceListResponse` = `{ items: ComplianceEntryRead[], total, page, page_size }`. `ComplianceEntryRead`: `id`, `client_id`, `kind`, `text`, `author_id?`, `is_active`, `created_at`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** List the compliance register.

### `POST /api/v1/clients/{client_id}/compliance`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `ComplianceEntryCreate`: `kind` (`ComplianceKind` **required**), `text` (**required**, `1..20000`).
- **Success `201`:** `ComplianceEntryRead`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Add an entry (author = current user); enqueues a rebuild.

### `PATCH /api/v1/clients/{client_id}/compliance/{entry_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `ComplianceEntryUpdate` (partial): `kind`, `text` (`1..20000`), `is_active` (`bool`).
- **Success `200`:** `ComplianceEntryRead`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Edit or (de)activate an entry; enqueues a rebuild.

### `DELETE /api/v1/clients/{client_id}/compliance/{entry_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `MessageResponse` = `{ detail: "Compliance entry deleted." }`.
- **Errors:** `401`; `404`.
- **Why/when:** Delete an entry; enqueues a rebuild.

### `POST /api/v1/clients/{client_id}/compliance/sync`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `IntelligenceStatus`.
- **Errors:** `401`; `404`.
- **Why/when:** Force the effective ruleset into the AI immediately (trigger a rebuild now).

---

## Module 15 — Plan (Kanban)
**Screen:** Plan (client). **Role:** All authenticated (scoped). **Frontend notes:** Board columns are the `TaskStatus` values (`todo` / `in_progress` / `blocked` / `done`). A kanban drag is just a `PATCH` of `status`; `PATCH` is partial so moving a card never clears its other fields. Filter by `status`, `category`, `assignee_id`.

Base path: `/clients/{client_id}/plan`.

### `GET /api/v1/clients/{client_id}/plan/tasks`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`; `status` (`TaskStatus`), `category` (`TaskCategory`: `strategy`/`creative`/`ads`/`content`/`analytics`/`compliance`/`admin`), `assignee_id` (`UUID`).
- **Success `200`:** `PlanTaskListResponse` = `{ items: PlanTaskRead[], total, page, page_size }`. `PlanTaskRead`: `id`, `client_id`, `title`, `description?`, `category`, `status`, `assignee_id?`, `due_date?`, `created_by?`, `created_at`, `updated_at`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Render / filter the kanban board.

### `POST /api/v1/clients/{client_id}/plan/tasks`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `PlanTaskCreate`: `title` (**required**, `1..200`), `description` (`≤20000`), `category` (`TaskCategory`, default `strategy`), `status` (`TaskStatus`, default `todo`), `assignee_id` (`UUID`), `due_date` (`date`).
- **Success `201`:** `PlanTaskRead`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Create a task card (creator = current user).

### `GET /api/v1/clients/{client_id}/plan/tasks/{task_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `PlanTaskRead`.
- **Errors:** `401`; `404`.
- **Why/when:** Task detail.

### `PATCH /api/v1/clients/{client_id}/plan/tasks/{task_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `PlanTaskUpdate` (partial): any of `title`, `description`, `category`, `status`, `assignee_id`, `due_date`.
- **Success `200`:** `PlanTaskRead`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Move across columns / reassign / relabel / reschedule.

### `DELETE /api/v1/clients/{client_id}/plan/tasks/{task_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `MessageResponse` = `{ detail: "Task deleted." }`.
- **Errors:** `401`; `404`.
- **Why/when:** Remove a card.

---

## Module 16 — Reports
**Screen:** Reports (client). **Role:** All authenticated (scoped). **Frontend notes:** These endpoints are the **report registry/history** — the frontend generates/renders the file (CSV/Excel/PDF), then records it here (config + optional `file_url` pointer). `scope`, `channels`, `sections`, and `save_to_outlook_draft` capture the "what did we generate and how do we deliver it" choices from the report screen.

Base path: `/clients/{client_id}/reports`.

### `GET /api/v1/clients/{client_id}/reports`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`; `kind` (`ReportKind`: `performance`/`compliance`/`strategy`/`executive`/`custom`).
- **Success `200`:** `ReportListResponse` = `{ items: ReportRead[], total, page, page_size }`. `ReportRead`: `id`, `client_id`, `kind`, `format`, `title`, `date_from`, `date_to`, `scope`, `channels`, `sections`, `save_to_outlook_draft`, `file_url`, `created_by`, `created_at`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Report history list.

### `POST /api/v1/clients/{client_id}/reports`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `ReportCreate`: `kind` (`ReportKind`, default `performance`), `format` (`ReportFormat`: `pdf`/`excel`/`visual`, default `pdf`), `title` (**required**, `1..200`), `date_from` (**required**), `date_to` (**required**, ≥ `date_from`), `scope` (`≤40`), `channels` (`str[]`), `sections` (`str[]`), `save_to_outlook_draft` (`bool`, default `false`), `file_url` (`str`).
- **Success `201`:** `ReportRead`.
- **Errors:** `400`/`422` (incl. date order); `401`; `404`.
- **Why/when:** Record a generated report.

### `GET /api/v1/clients/{client_id}/reports/{report_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `ReportRead`.
- **Errors:** `401`; `404`.
- **Why/when:** Fetch one registry entry.

### `PATCH /api/v1/clients/{client_id}/reports/{report_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `ReportUpdate` (partial): `title` (`1..200`), `file_url`, `save_to_outlook_draft` (`bool`).
- **Success `200`:** `ReportRead`.
- **Errors:** `400`/`422`; `401`; `404`.
- **Why/when:** Attach the rendered file / tweak delivery after generation.

### `DELETE /api/v1/clients/{client_id}/reports/{report_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `MessageResponse` = `{ detail: "Report deleted." }`.
- **Errors:** `401`; `404`.
- **Why/when:** Delete a registry entry.

---

## Module 17 — Integrations
**Screen:** Integration (client). **Role:** All authenticated (scoped) — any user who can see the client may manage its connectors. **Frontend notes:** The connector `key` is one of `ga4` / `search_console` / `google_ads` / `google_lsa` / `meta` / `linkedin`. **Meta and Google Ads use the real per-client OAuth2 flow** (meeting decision): call `oauth/start` → redirect the browser to `authorization_url` (keep the returned `state`) → on the provider callback, call `oauth/complete` with `{code, state}`; the token is stored **encrypted** server-side and never returned. Other providers use the placeholder `connect`. `sync` pulls live insights into analytics; `disconnect` resets the connector. Token columns are never exposed in responses.

Base path: `/clients/{client_id}/integrations`.

### `GET /api/v1/clients/{client_id}/integrations`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `IntegrationListResponse` = `{ items: IntegrationRead[] }` (full connector catalog; not paginated). `IntegrationRead`: `id`, `client_id`, `key` (`IntegrationKey`), `status` (`IntegrationStatus`), `account_label?`, `external_account_id?`, `scopes?`, `last_sync_at?`, `last_error?`, `created_at`, `updated_at`.
- **Errors:** `401`; `404`.
- **Why/when:** List connectors and their connection state.

### `GET /api/v1/clients/{client_id}/integrations/{key}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `IntegrationRead`.
- **Errors:** `401`; `404`; `422` invalid `key`.
- **Why/when:** One connector's state.

### `POST /api/v1/clients/{client_id}/integrations/{key}/oauth/start`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `OAuthStartResponse` = `{ authorization_url, state }`.
- **Errors:** `401`; `404`; `422` invalid `key`; `503` provider unconfigured.
- **Why/when:** Begin real OAuth (Meta / Google Ads) — redirect the browser to `authorization_url`.

### `POST /api/v1/clients/{client_id}/integrations/{key}/oauth/complete`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `OAuthCompleteRequest` (**strict**): `code` (**required**, `1..2048`), `state` (**required**, `1..1024`).
- **Success `200`:** `IntegrationRead` (status now `connected`; token stored encrypted).
- **Errors:** `401`; `404`; `409` state/CSRF mismatch or already connected; `422`; `503` token exchange failed.
- **Why/when:** Finish OAuth (callback) — exchange `code` for a token, validating `state`.

### `POST /api/v1/clients/{client_id}/integrations/{key}/sync`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `IntegrationRead` (updated `last_sync_at` / `last_error`).
- **Errors:** `401`; `404`; `409` not connected; `422`; `503` provider fetch failed.
- **Why/when:** Pull live insights into analytics.

### `POST /api/v1/clients/{client_id}/integrations/{key}/connect`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `IntegrationConnectRequest` (all optional): `account_label` (`≤200`), `external_account_id` (`≤160`), `scopes` (`≤2000`, comma-separated).
- **Success `200`:** `IntegrationRead` (status → `connected`).
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Placeholder connect for non-real-OAuth providers.

### `POST /api/v1/clients/{client_id}/integrations/{key}/disconnect`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `IntegrationRead` (status → `disconnected`).
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Reset a connector (clears stored token/connection).

---

## Module 18 — Uploads
**Screen:** Global file service — used by the onboarding **Documents** step, report file attachments, and conversation attachments. **Role:** All authenticated; **owner-scoped** (a non-admin sees only their own uploads; admins see all; inaccessible → `404`). **Frontend notes:** Upload the file bytes here first (multipart), then reference the returned **`storage_key`** wherever a feature needs it (e.g. the onboarding `documents[]`). Download URLs are **short-lived presigned S3 URLs (~15 min)** — fetch a fresh one via `GET /uploads/{id}` right before use rather than caching. If storage is unconfigured server-side, calls return `503`.

Base path: `/uploads`.

### `POST /api/v1/uploads`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Request payload:** `multipart/form-data`: `file` (**required**, `UploadFile`) — content-type must be on the allow-list (PDF, PNG/JPEG/GIF/WEBP/SVG, TXT/MD/CSV, DOC/DOCX, PPT/PPTX, XLS/XLSX, ZIP by default); **size cap 20 MB** default. `feature` (optional `str` form field — origin tag, e.g. `onboarding`).
- **Success `201`:** `UploadRead` = `{ id, original_filename, content_type?, size_bytes, feature?, storage_key, download_url?, created_at }`.
- **Errors:** `400` empty/zero-byte file; `401`; `409` write race; `413` too large; `415` type not allowed; `422` missing `file`; `503` storage unconfigured.
- **Why/when:** Upload a file (streamed to S3).

### `GET /api/v1/uploads/{upload_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `UploadRead` with a **fresh** presigned `download_url`.
- **Errors:** `401`; `404` not found / not owned; `422`.
- **Why/when:** Get metadata + a fresh download link.

### `DELETE /api/v1/uploads/{upload_id}`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `MessageResponse` = `{ detail: "Upload deleted." }`.
- **Errors:** `401`; `404`; `409` delete failure.
- **Why/when:** Delete the S3 object + its record.

---

## Module 19 — Notifications
**Screen:** Notification Center (global, app shell). **Role:** All authenticated; **per-user** (each caller sees only their own). **Frontend notes:** Poll `unread-count` for the badge; open the center with `GET /notifications` (optionally `unread_only`); mark one read on click, or "clear all" with `read-all`. `link` (when present) is a deep-link the frontend can route to.

Base path: `/notifications`.

### `GET /api/v1/notifications`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Query params:** `page`, `page_size`; `unread_only` (`bool`, default `false`).
- **Success `200`:** `NotificationListResponse` = `{ items: NotificationRead[], total, unread, page, page_size }` (note the extra `unread` count). `NotificationRead`: `id`, `client_id?`, `kind`, `level` (`info`/`warning`/`critical`), `title`, `body?`, `link?`, `read_at?`, `created_at`.
- **Errors:** `401`; `422`.
- **Why/when:** Render the notification center.

### `GET /api/v1/notifications/unread-count`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `UnreadCount` = `{ unread: int }`.
- **Errors:** `401`.
- **Why/when:** Badge count.

### `POST /api/v1/notifications/read-all`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `MessageResponse` = `{ detail: "Marked N notification(s) read." }`.
- **Errors:** `401`.
- **Why/when:** Clear all.

### `POST /api/v1/notifications/{notification_id}/read`
- **Auth:** Authenticated (any role). **Rate limited:** No.
- **Success `200`:** `NotificationRead` (`read_at` now set).
- **Errors:** `401`; `404`; `422`.
- **Why/when:** Mark one read on open.

---

## Module 20 — Automation / Platform Ops (Admin)
**Screen:** No dedicated end-user screen — these are **admin, platform-wide** operations that also run automatically on a cadence by the scheduler process (`python -m app.scheduler`). Expose them behind an admin "Ops / Automation" panel if you want manual triggers. **Role:** Admin only; **not** assignment-scoped — an admin can act on any client. **Frontend notes:** Use the digest endpoints to build an admin overview of all clients (open alerts, integration/onboarding status). The two `POST` sweeps are manual "run now" triggers of jobs the scheduler already runs.

Base path: `/automation`.

### `POST /api/v1/automation/watchdog/run`
- **Auth:** Admin only. **Rate limited:** No.
- **Success `200`:** `WatchdogSweepResult` = `{ clients, opened, updated, auto_resolved, per_client: [{ client_id, client_name, opened, updated, auto_resolved }] }`.
- **Errors:** `401`; `403`.
- **Why/when:** Run the KPI watchdog across every active client now.

### `POST /api/v1/automation/integrations/sync`
- **Auth:** Admin only. **Rate limited:** No.
- **Success `200`:** `SyncSweepResult` = `{ clients, synced, failed, details: [{ client_id, client_name, key, ok, error? }] }`.
- **Errors:** `401`; `403`.
- **Why/when:** Sync every connected integration across all active clients now.

### `GET /api/v1/automation/digest`
- **Auth:** Admin only. **Rate limited:** No.
- **Query params:** None *(returns `{ items, total }`, not paginated)*.
- **Success `200`:** `DigestList` = `{ items: ClientDigest[], total }`.
- **Errors:** `401`; `403`.
- **Why/when:** Daily digest for all active clients (admin overview).

### `GET /api/v1/automation/clients/{client_id}/digest`
- **Auth:** Admin only. **Rate limited:** No.
- **Success `200`:** `ClientDigest` = `{ client_id, client_name, status, onboarding_percent, campaign_count, open_alerts, high, medium, low, top_alerts: [{ id, title, severity, metric? }], connected_integrations: str[], pending_integrations: str[], generated_at }`.
- **Errors:** `401`; `403`; `404` client not found.
- **Why/when:** On-demand digest for one client (admin drill-down).

---

## Module 21 — AI Usage (Admin)
**Screen:** Token Usage (agency admin). **Role:** Admin only for platform lists/summary; the per-client summary also allows a non-admin **assigned** to that client. **Frontend notes:** Use `/ai-usage/summary` for the platform token/cost dashboard (breakdowns by feature/model/client/user + daily series); `/ai-usage` for the drill-down event log with filters; `/ai-usage/clients/{id}/summary` for a client-scoped cost view. All figures come from recorded AI calls.

Base path: `/ai-usage`.

### `GET /api/v1/ai-usage`
- **Auth:** Admin only. **Rate limited:** No.
- **Query params:** `page`, `page_size`; `client_id`, `user_id`, `feature`, `model`, `status` (`success`/`error`), `start` (`datetime`), `end` (`datetime`).
- **Success `200`:** `AiUsageListResponse` = `{ items: AiUsageEventRead[], total, page, page_size }`. `AiUsageEventRead`: `id`, `created_at`, `actor_user_id?`, `client_id?`, `feature`, `provider`, `model`, `operation`, token counts (`input_tokens`, `output_tokens`, `cache_write_tokens`, `cache_read_tokens`, `total_tokens`), costs (`input_cost`, `output_cost`, `cache_cost`, `total_cost`), `currency`, `priced`, `status`, `error?`, `duration_ms?`, `request_id?`.
- **Errors:** `401`; `403`; `422`.
- **Why/when:** Per-request usage/cost log (observability, billing).

### `GET /api/v1/ai-usage/summary`
- **Auth:** Admin only. **Rate limited:** No.
- **Query params:** `client_id`, `user_id`, `feature`, `model`, `status`, `start`, `end` (no pagination).
- **Success `200`:** `PlatformUsageSummary` = `{ totals: UsageTotals, by_feature, by_model, by_client, by_user: UsageGroupRow[], daily: DailyUsage[] }`. `UsageTotals`: `requests`, token counts, `total_cost`, `currency`. `UsageGroupRow`: `key?`, `requests`, `total_tokens`, `total_cost`. `DailyUsage`: `day`, `requests`, `total_tokens`, `total_cost`.
- **Errors:** `401`; `403`; `422`.
- **Why/when:** Platform token/cost analytics dashboard.

### `GET /api/v1/ai-usage/clients/{client_id}/summary`
- **Auth:** Admin **or** a non-admin assigned to the client (inaccessible → `404`). **Rate limited:** No.
- **Query params:** `start`, `end`, `feature` (no pagination).
- **Success `200`:** `ClientUsageSummary` = `{ client_id, totals: UsageTotals, by_feature, by_model: UsageGroupRow[], daily: DailyUsage[] }`.
- **Errors:** `401`; `404`; `422`.
- **Why/when:** One client's AI usage/cost.

---

## Module 22 — Audit Log (Admin)
**Screen:** Audit Logs (agency admin). **Role:** Admin only. **Frontend notes:** Every API request is recorded (actor, action, entity, status, ip, duration). Actions are free-form dotted strings (e.g. `report.pdf.exported`, `recommendation.accepted`, `integration.connect`). Mutating edits (e.g. client update) also record a **field-level `changes` diff** (`{ field: { before, after } }`) — render this as a "what changed" view. Filter by `action` (substring), `entity`, `actor_user_id`, `client_id`.

Base path: `/audit`.

### `GET /api/v1/audit`
- **Auth:** Admin only. **Rate limited:** No.
- **Query params:** `page`, `page_size`; `action` (`str`, substring), `entity` (`str`, exact, e.g. `clients`), `actor_user_id` (`UUID`), `client_id` (`UUID`).
- **Success `200`:** `AuditLogListResponse` = `{ items: AuditLogRead[], total, page, page_size }`. `AuditLogRead`: `id`, `actor_user_id?`, `client_id?`, `entity`, `entity_id?`, `action`, `target_label?`, `meta?`, `changes?` (`{ field: { before, after } }`), `created_at`.
- **Errors:** `401`; `403`; `422`.
- **Why/when:** Query the immutable "who did what" trail for compliance/debugging.

---

## Appendix A — Enum reference

| Enum | Values | Used by |
| --- | --- | --- |
| `UserRole` | `admin`, `manager`, `user` | Users, auth |
| `ClientStatus` | `draft`, `active`, `inactive`, `paused`, `onboarding`, `archived` | Clients |
| `DocumentKind` | `brand`, `compliance`, `goals`, `contract`, `brief`, `creative`, `other` | Onboarding documents |
| `SocialPlatform` | `instagram`, `facebook`, `youtube`, `tiktok`, `x`, `linkedin`, `pinterest`, `google`, `email`, `other` | Analytics, Calendar |
| `CampaignStatus` | `draft`, `active`, `paused`, `ended` | Campaigns |
| `AdObjective` | `awareness`, `traffic`, `engagement`, `leads`, `conversions` | Campaigns, Calendar ads |
| `AlertStatus` | `open`, `acknowledged`, `resolved` | Alerts |
| `AlertSeverity` | `low`, `medium`, `high` | Alerts, Dashboard |
| `AlertKind` | `alert`, `opportunity` | Alerts, Watchdog |
| `EventType` | `campaign`, `email`, `ad`, `review`, `content`, `meeting` | Calendar |
| `EventStage` | `draft`, `scheduled`, `published`, `archived` | Calendar |
| `ApprovalStatus` | `approved`, `pending`, `changes_requested`, `rejected` | Calendar approval |
| `ConversationSource` | `email`, `sms`, `whatsapp`, `instagram`, `facebook`, `webform`, `internal` | Conversations |
| `MessageFolder` | `inbox`, `sent`, `drafts`, `archive`, `spam`, `trash` | Conversations |
| `RecipientKind` | `to`, `cc`, `bcc` | Conversations |
| `ComplianceKind` | `brand_voice`, `banned`, `required`, `rule`, `note` | Compliance |
| `TaskStatus` | `todo`, `in_progress`, `blocked`, `done` | Plan (kanban columns) |
| `TaskCategory` | `strategy`, `creative`, `ads`, `content`, `analytics`, `compliance`, `admin` | Plan |
| `ReportKind` | `performance`, `compliance`, `strategy`, `executive`, `custom` | Reports |
| `ReportFormat` | `pdf`, `excel`, `visual` | Reports |
| `RecommendationDecision` | `accepted`, `modified`, `rejected` | Dashboard recommendations |
| `IntegrationKey` | `ga4`, `search_console`, `google_ads`, `google_lsa`, `meta`, `linkedin` | Integrations |
| `IntegrationStatus` | `disconnected`, `connected`, `error` (+ pending states) | Integrations |
| `NotificationLevel` | `info`, `warning`, `critical` | Notifications |

---

*Generated for the InWork MarketingOS backend. All paths are under `/api/v1`; all protected endpoints require `Authorization: Bearer <token>`. For live, always-current schemas see `/openapi.json` and the Swagger UI at `/docs`.*
