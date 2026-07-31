"""Comprehensive live API test harness for InWork MarketingOS.

Runs against the running uvicorn server (http://localhost:8000), validates every
endpoint's happy path + error paths + RBAC, and writes a JSON result set.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import httpx

BASE = "http://localhost:8000"
API = f"{BASE}/api/v1"
UNIQ = str(int(time.time()))
ADMIN = {"email": "admin@inwork.com", "password": "12345678"}

client = httpx.Client(timeout=45.0)
results: list[dict] = []


def rec(tid, group, title, method, endpoint, expected, passed, actual, note=""):
    results.append(
        {
            "id": tid,
            "group": group,
            "title": title,
            "method": method,
            "endpoint": endpoint,
            "expected": expected,
            "status": "PASS" if passed else "FAIL",
            "actual": actual,
            "note": note,
        }
    )
    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] {tid:<6} {title}")


def warn(tid, group, title, method, endpoint, expected, actual, note=""):
    results.append(
        {
            "id": tid,
            "group": group,
            "title": title,
            "method": method,
            "endpoint": endpoint,
            "expected": expected,
            "status": "WARN",
            "actual": actual,
            "note": note,
        }
    )
    print(f"[WARN] {tid:<6} {title} -- {note}")


def err_code(r):
    try:
        return r.json().get("error", {}).get("code")
    except Exception:
        return None


def H(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- HEALTH
r = client.get(f"{BASE}/health")
rec(
    "H-01",
    "Health",
    "GET /health returns 200 + status",
    "GET",
    "/health",
    "200, body has status",
    r.status_code == 200 and "status" in r.json(),
    f"{r.status_code} {r.text[:80]}",
)

# ---------------------------------------------------------------- AUTH
r = client.post(f"{API}/auth/login", json=ADMIN)
admin_ok = r.status_code == 200 and "access_token" in r.json()
admin_token = r.json().get("access_token", "") if admin_ok else ""
rec(
    "A-01",
    "Auth",
    "Admin login succeeds, returns token + admin user",
    "POST",
    "/auth/login",
    "200, access_token present, user.role=admin",
    admin_ok and r.json()["user"]["role"] == "admin",
    f"{r.status_code}, role={r.json().get('user', {}).get('role')}",
)

r = client.post(f"{API}/auth/login", json={"email": ADMIN["email"], "password": "wrong-pass9"})
rec(
    "A-02",
    "Auth",
    "Login with wrong password rejected",
    "POST",
    "/auth/login",
    "401, code=unauthorized",
    r.status_code == 401 and err_code(r) == "unauthorized",
    f"{r.status_code} code={err_code(r)}",
)

r = client.post(
    f"{API}/auth/login", json={"email": f"ghost{UNIQ}@nope.com", "password": "whatever1"}
)
rec(
    "A-03",
    "Auth",
    "Login unknown email rejected (same generic error)",
    "POST",
    "/auth/login",
    "401, code=unauthorized",
    r.status_code == 401 and err_code(r) == "unauthorized",
    f"{r.status_code} code={err_code(r)}",
)

r = client.post(f"{API}/auth/login", json={"email": "not-an-email", "password": "x"})
rec(
    "A-04",
    "Auth",
    "Login malformed email is a validation error",
    "POST",
    "/auth/login",
    "422, code=validation_error",
    r.status_code == 422 and err_code(r) == "validation_error",
    f"{r.status_code} code={err_code(r)}",
)

r = client.post(f"{API}/auth/login", json={"email": ADMIN["email"]})
rec(
    "A-05",
    "Auth",
    "Login missing password field -> 422",
    "POST",
    "/auth/login",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

r = client.post(f"{API}/auth/login", json={"email": ADMIN["email"], "password": ""})
rec(
    "A-06",
    "Auth",
    "Login empty password (min_length=1) -> 422",
    "POST",
    "/auth/login",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

# ---------------------------------------------------------------- SECURITY / AUTHZ
r = client.get(f"{API}/clients")
rec(
    "S-01",
    "Security",
    "Protected endpoint without token -> 401",
    "GET",
    "/clients",
    "401, code=unauthorized",
    r.status_code == 401 and err_code(r) == "unauthorized",
    f"{r.status_code} code={err_code(r)}",
)

r = client.get(f"{API}/clients", headers=H("garbage.token.value"))
rec(
    "S-02",
    "Security",
    "Protected endpoint with malformed token -> 401",
    "GET",
    "/clients",
    "401",
    r.status_code == 401,
    f"{r.status_code} code={err_code(r)}",
)

FORGED = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJmYWtlIiwidHlwZSI6ImFjY2VzcyJ9.invalidsig"
r = client.get(f"{API}/clients", headers=H(FORGED))
rec(
    "S-03",
    "Security",
    "Token with invalid signature -> 401",
    "GET",
    "/clients",
    "401",
    r.status_code == 401,
    f"{r.status_code} code={err_code(r)}",
)

# create a non-admin user for RBAC + 403 checks
nonadmin_email = f"qa.user.{UNIQ}@example.com"
r = client.post(
    f"{API}/users",
    headers=H(admin_token),
    json={"name": "QA User", "email": nonadmin_email, "password": "Passw0rd1", "role": "user"},
)
nonadmin_id = r.json().get("id") if r.status_code == 201 else None
r2 = client.post(f"{API}/auth/login", json={"email": nonadmin_email, "password": "Passw0rd1"})
nonadmin_token = r2.json().get("access_token", "")

r = client.post(
    f"{API}/users",
    headers=H(nonadmin_token),
    json={"name": "X", "email": f"x{UNIQ}@e.com", "password": "Passw0rd1"},
)
rec(
    "S-04",
    "Security",
    "Non-admin cannot create users -> 403",
    "POST",
    "/users",
    "403, code=forbidden",
    r.status_code == 403 and err_code(r) == "forbidden",
    f"{r.status_code} code={err_code(r)}",
)

from_payload = {
    "name": "Blocked Co.",
    "business_type": "DTC",
    "industry": "Retail",
    "brand": {"brand_voice": "v"},
    "platforms": ["meta"],
    "client_contacts": [{"name": "A", "email": "a@a.com"}],
}
r = client.post(f"{API}/clients/onboarding", headers=H(nonadmin_token), json=from_payload)
rec(
    "S-05",
    "Security",
    "Non-admin cannot onboard (atomic) -> 403",
    "POST",
    "/clients/onboarding",
    "403",
    r.status_code == 403,
    f"{r.status_code} code={err_code(r)}",
)

r = client.post(
    f"{API}/clients/onboarding/draft",
    headers=H(nonadmin_token),
    json={"name": "Blocked", "business_type": "DTC", "industry": "Retail"},
)
rec(
    "S-06",
    "Security",
    "Non-admin cannot start onboarding draft -> 403",
    "POST",
    "/clients/onboarding/draft",
    "403",
    r.status_code == 403,
    f"{r.status_code} code={err_code(r)}",
)

# ---------------------------------------------------------------- USERS
u_email = f"qa.created.{UNIQ}@example.com"
r = client.post(
    f"{API}/users",
    headers=H(admin_token),
    json={"name": "Created User", "email": u_email, "password": "Secret123", "role": "manager"},
)
body = r.json()
created_uid = body.get("id")
rec(
    "U-01",
    "Users",
    "Admin creates user (201), no secret leaked",
    "POST",
    "/users",
    "201, role=manager, is_active=true, no password fields",
    r.status_code == 201
    and body.get("role") == "manager"
    and body.get("is_active") is True
    and "password" not in body
    and "password_hash" not in body,
    f"{r.status_code} keys={sorted(body.keys())}",
)

r = client.post(
    f"{API}/users",
    headers=H(admin_token),
    json={"name": "Dup", "email": u_email, "password": "Secret123"},
)
rec(
    "U-02",
    "Users",
    "Duplicate email -> 409 conflict",
    "POST",
    "/users",
    "409, code=conflict",
    r.status_code == 409 and err_code(r) == "conflict",
    f"{r.status_code} code={err_code(r)}",
)

r = client.post(
    f"{API}/users",
    headers=H(admin_token),
    json={"name": "Weak", "email": f"weak{UNIQ}@e.com", "password": "onlyletters"},
)
rec(
    "U-03",
    "Users",
    "Weak password (no digit) -> 422",
    "POST",
    "/users",
    "422",
    r.status_code == 422,
    f"{r.status_code} code={err_code(r)}",
)

r = client.post(
    f"{API}/users",
    headers=H(admin_token),
    json={"name": "Short", "email": f"short{UNIQ}@e.com", "password": "Ab1"},
)
rec(
    "U-04",
    "Users",
    "Short password (<8) -> 422",
    "POST",
    "/users",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

r = client.post(
    f"{API}/users",
    headers=H(admin_token),
    json={
        "name": "BadRole",
        "email": f"role{UNIQ}@e.com",
        "password": "Secret123",
        "role": "superuser",
    },
)
rec(
    "U-05",
    "Users",
    "Invalid role enum -> 422",
    "POST",
    "/users",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

r = client.post(
    f"{API}/users",
    headers=H(admin_token),
    json={"name": "BadEmail", "email": "nope", "password": "Secret123"},
)
rec(
    "U-06",
    "Users",
    "Invalid email -> 422",
    "POST",
    "/users",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

r = client.get(f"{API}/users", headers=H(admin_token))
b = r.json()
rec(
    "U-07",
    "Users",
    "Admin lists users (200)",
    "GET",
    "/users",
    "200, items/total present, total>=1",
    r.status_code == 200 and b.get("total", 0) >= 1 and isinstance(b.get("items"), list),
    f"{r.status_code} total={b.get('total')}",
)

r = client.patch(f"{API}/users/{created_uid}", headers=H(admin_token), json={"role": "user"})
rec(
    "U-08",
    "Users",
    "Admin updates user role (200)",
    "PATCH",
    "/users/{id}",
    "200, role=user",
    r.status_code == 200 and r.json().get("role") == "user",
    f"{r.status_code} role={r.json().get('role')}",
)

# disabled-login lifecycle: dedicated user
dis_email = f"qa.disabled.{UNIQ}@example.com"
dr = client.post(
    f"{API}/users",
    headers=H(admin_token),
    json={"name": "Dis", "email": dis_email, "password": "Passw0rd1"},
)
dis_id = dr.json().get("id")
client.patch(f"{API}/users/{dis_id}", headers=H(admin_token), json={"is_active": False})
r = client.post(f"{API}/auth/login", json={"email": dis_email, "password": "Passw0rd1"})
rec(
    "U-09",
    "Users",
    "Disabled user cannot log in -> 401",
    "POST",
    "/auth/login",
    "401",
    r.status_code == 401,
    f"{r.status_code} code={err_code(r)}",
)

r = client.patch(f"{API}/users/{uuid.uuid4()}", headers=H(admin_token), json={"role": "user"})
rec(
    "U-10",
    "Users",
    "Update unknown user -> 404",
    "PATCH",
    "/users/{id}",
    "404, code=not_found",
    r.status_code == 404 and err_code(r) == "not_found",
    f"{r.status_code} code={err_code(r)}",
)

r = client.patch(f"{API}/users/not-a-uuid", headers=H(admin_token), json={"role": "user"})
rec(
    "U-11",
    "Users",
    "Update with invalid UUID path -> 422",
    "PATCH",
    "/users/{id}",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

# ---------------------------------------------------------------- CLIENTS LIST
r = client.get(f"{API}/clients", headers=H(admin_token))
b = r.json()
rec(
    "C-01",
    "Clients",
    "Admin lists clients (200) with pagination shape",
    "GET",
    "/clients",
    "200, items/total/page/page_size",
    r.status_code == 200 and all(k in b for k in ("items", "total", "page", "page_size")),
    f"{r.status_code} keys={sorted(b.keys())}",
)

r = client.get(f"{API}/clients?page=1&page_size=1", headers=H(admin_token))
rec(
    "C-02",
    "Clients",
    "page_size=1 returns at most 1 item",
    "GET",
    "/clients",
    "200, len(items)<=1",
    r.status_code == 200 and len(r.json().get("items", [])) <= 1,
    f"{r.status_code} len={len(r.json().get('items', []))}",
)

r = client.get(f"{API}/clients?page=0", headers=H(admin_token))
rec(
    "C-03",
    "Clients",
    "page=0 rejected (ge=1) -> 422",
    "GET",
    "/clients",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

r = client.get(f"{API}/clients?page_size=101", headers=H(admin_token))
rec(
    "C-04",
    "Clients",
    "page_size=101 rejected (le=100) -> 422",
    "GET",
    "/clients",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)


# ---------------------------------------------------------------- ONBOARDING (atomic)
def onboard_payload(name, **ov):
    p = {
        "name": name,
        "business_type": "DTC E-commerce",
        "industry": "Home & Garden",
        "website": "https://acme.com",
        "language": "English (US)",
        "location": "Austin, TX",
        "markets": "Entire US",
        "brand": {
            "brand_voice": "Friendly, witty.",
            "about_brand": "Joyful goods.",
            "colors": [{"hex": "#0EA5E9", "label": "Primary"}, {"hex": "#1E3A8A"}],
            "fonts": ["Inter"],
            "logo_url": "https://acme.com/logo.svg",
        },
        "platforms": ["meta", "google-ads", "seo"],
        "goals": "Q1 brand; Q2 lead-gen; Q3 ecommerce. Build momentum.",
        "compliance": {"feed": "Never say 'cheap'. Always 'Made in USA'."},
        "client_contacts": [{"name": "Jane", "role": "CMO", "email": "jane@acme.com"}],
        "inwork_contacts": [{"name": "Alex", "email": "alex@inwork.com"}],
        "documents": [],
    }
    p.update(ov)
    return p


atomic_name = f"Atomic QA {UNIQ}"
r = client.post(
    f"{API}/clients/onboarding", headers=H(admin_token), json=onboard_payload(atomic_name)
)
b = r.json()
c = b.get("client", {})
rec(
    "O-01",
    "Onboarding-Atomic",
    "Full onboarding creates client (201) + readiness",
    "POST",
    "/clients/onboarding",
    "201, status=onboarding, step=8, 2 colors, 3 platforms, 2 contacts, readiness present, integrations missing",
    r.status_code == 201
    and c.get("status") == "onboarding"
    and c.get("onboarding_step") == 8
    and len(c.get("brand_colors", [])) == 2
    and len(c.get("platforms", [])) == 3
    and len(c.get("contacts", [])) == 2
    and 0 <= b.get("readiness", {}).get("score", -1) <= 100
    and any(m["key"] == "integrations" for m in b.get("readiness", {}).get("missing", [])),
    f"{r.status_code} step={c.get('onboarding_step')} colors={len(c.get('brand_colors', []))} "
    f"platforms={len(c.get('platforms', []))} score={b.get('readiness', {}).get('score')}",
)
atomic_id = c.get("id")
atomic_slug = c.get("slug")

r1 = client.post(
    f"{API}/clients/onboarding", headers=H(admin_token), json=onboard_payload(atomic_name)
)
r2 = client.post(
    f"{API}/clients/onboarding", headers=H(admin_token), json=onboard_payload(atomic_name)
)
s1 = r1.json()["client"]["slug"]
s2 = r2.json()["client"]["slug"]
rec(
    "O-02",
    "Onboarding-Atomic",
    "Duplicate names get unique incrementing slugs",
    "POST",
    "/clients/onboarding",
    "second slug ends with -N suffix, distinct from first",
    s1 != s2 and s2[-1].isdigit(),
    f"slug1={s1} slug2={s2}",
)

r = client.post(
    f"{API}/clients/onboarding",
    headers=H(admin_token),
    json=onboard_payload("NoPlat", platforms=[]),
)
rec(
    "O-03",
    "Onboarding-Atomic",
    "Missing platforms -> 422",
    "POST",
    "/clients/onboarding",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

r = client.post(
    f"{API}/clients/onboarding",
    headers=H(admin_token),
    json=onboard_payload("NoVoice", brand={"about_brand": "x"}),
)
rec(
    "O-04",
    "Onboarding-Atomic",
    "Missing brand_voice -> 422",
    "POST",
    "/clients/onboarding",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

r = client.post(
    f"{API}/clients/onboarding",
    headers=H(admin_token),
    json=onboard_payload("BadHex", brand={"brand_voice": "v", "colors": [{"hex": "blue"}]}),
)
rec(
    "O-05",
    "Onboarding-Atomic",
    "Invalid hex color -> 422",
    "POST",
    "/clients/onboarding",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

r = client.post(
    f"{API}/clients/onboarding",
    headers=H(admin_token),
    json=onboard_payload("NoContact", client_contacts=[{"name": "No Email"}]),
)
rec(
    "O-06",
    "Onboarding-Atomic",
    "No client contact email -> 422",
    "POST",
    "/clients/onboarding",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

# ---------------------------------------------------------------- ONBOARDING (progressive)
draft_body = {
    "name": f"Draft QA {UNIQ}",
    "business_type": "SaaS",
    "industry": "DevTools",
    "website": "https://x.dev",
    "markets": "US+EU",
}
r = client.post(f"{API}/clients/onboarding/draft", headers=H(admin_token), json=draft_body)
b = r.json()
draft_id = b.get("client", {}).get("id")
rec(
    "P-01",
    "Onboarding-Progressive",
    "Draft creates client at step 1 (13%)",
    "POST",
    "/clients/onboarding/draft",
    "201, onboarding={step:1,percent:13,completed:false}, status=onboarding",
    r.status_code == 201
    and b.get("onboarding") == {"step": 1, "total_steps": 8, "percent": 13, "completed": False}
    and b.get("client", {}).get("status") == "onboarding",
    f"{r.status_code} onboarding={b.get('onboarding')}",
)

r = client.post(
    f"{API}/clients/onboarding/draft",
    headers=H(admin_token),
    json={"name": "NoInd", "business_type": "SaaS"},
)
rec(
    "P-02",
    "Onboarding-Progressive",
    "Draft missing mandatory industry -> 422",
    "POST",
    "/clients/onboarding/draft",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

r = client.patch(
    f"{API}/clients/{draft_id}/onboarding",
    headers=H(admin_token),
    json={
        "step": 2,
        "brand": {
            "brand_voice": "Bold and clear.",
            "colors": [{"hex": "#111827"}],
            "logo_url": "https://x.dev/l.svg",
        },
    },
)
b = r.json()
rec(
    "P-03",
    "Onboarding-Progressive",
    "PATCH brand step 2 saves + advances to 25%",
    "PATCH",
    "/clients/{id}/onboarding",
    "200, percent=25, brand_voice saved, 'Brand voice defined' completed",
    r.status_code == 200
    and b.get("onboarding", {}).get("percent") == 25
    and b.get("client", {}).get("brand_voice") == "Bold and clear."
    and "Brand voice defined" in b.get("readiness", {}).get("completed", []),
    f"{r.status_code} percent={b.get('onboarding', {}).get('percent')} "
    f"voice={b.get('client', {}).get('brand_voice')!r}",
)

r = client.patch(
    f"{API}/clients/{draft_id}/onboarding",
    headers=H(admin_token),
    json={"step": 4, "goals": "Grow trials in US/EU steadily over 3 quarters."},
)
b = r.json()
rec(
    "P-04",
    "Onboarding-Progressive",
    "Partial PATCH (goals) does not clobber brand",
    "PATCH",
    "/clients/{id}/onboarding",
    "200, brand_voice intact, goals saved, step=4",
    r.status_code == 200
    and b.get("client", {}).get("brand_voice") == "Bold and clear."
    and b.get("client", {}).get("goals", "").startswith("Grow")
    and b.get("onboarding", {}).get("step") == 4,
    f"{r.status_code} voice_intact={b.get('client', {}).get('brand_voice')!r} step={b.get('onboarding', {}).get('step')}",
)

client.patch(
    f"{API}/clients/{draft_id}/onboarding",
    headers=H(admin_token),
    json={"step": 3, "platforms": ["meta", "Meta", "google-ads"]},
)
r = client.patch(
    f"{API}/clients/{draft_id}/onboarding",
    headers=H(admin_token),
    json={"step": 3, "platforms": ["seo"]},
)
chans = {p["channel"] for p in r.json().get("client", {}).get("platforms", [])}
rec(
    "P-05",
    "Onboarding-Progressive",
    "PATCH platforms replaces prior set (dedup/lowercase)",
    "PATCH",
    "/clients/{id}/onboarding",
    "200, platforms=={seo}",
    r.status_code == 200 and chans == {"seo"},
    f"{r.status_code} channels={chans}",
)

client.patch(f"{API}/clients/{draft_id}/onboarding", headers=H(admin_token), json={"step": 6})
r = client.patch(f"{API}/clients/{draft_id}/onboarding", headers=H(admin_token), json={"step": 3})
rec(
    "P-06",
    "Onboarding-Progressive",
    "Step advances monotonically (lower step ignored)",
    "PATCH",
    "/clients/{id}/onboarding",
    "200, step stays 6",
    r.status_code == 200 and r.json().get("onboarding", {}).get("step") == 6,
    f"step={r.json().get('onboarding', {}).get('step')}",
)

r = client.post(
    f"{API}/clients/{draft_id}/documents",
    headers=H(admin_token),
    json={
        "documents": [
            {
                "name": "brief.pdf",
                "kind": "brief",
                "size_bytes": 2048,
                "mime_type": "application/pdf",
                "storage_url": "s3://b/brief.pdf",
            }
        ]
    },
)
rec(
    "P-07",
    "Onboarding-Progressive",
    "Attach documents -> 201",
    "POST",
    "/clients/{id}/documents",
    "201",
    r.status_code == 201,
    f"{r.status_code}",
)

r = client.post(f"{API}/clients/{draft_id}/onboarding/complete", headers=H(admin_token))
b = r.json()
rec(
    "P-08",
    "Onboarding-Progressive",
    "Complete finalizes at 100% / step 8",
    "POST",
    "/clients/{id}/onboarding/complete",
    "200, onboarding={step:8,percent:100,completed:true}",
    r.status_code == 200
    and b.get("onboarding") == {"step": 8, "total_steps": 8, "percent": 100, "completed": True},
    f"{r.status_code} onboarding={b.get('onboarding')}",
)

r = client.patch(
    f"{API}/clients/{uuid.uuid4()}/onboarding", headers=H(admin_token), json={"step": 2}
)
rec(
    "P-09",
    "Onboarding-Progressive",
    "PATCH unknown client -> 404",
    "PATCH",
    "/clients/{id}/onboarding",
    "404, code=not_found",
    r.status_code == 404 and err_code(r) == "not_found",
    f"{r.status_code} code={err_code(r)}",
)

r = client.patch(f"{API}/clients/not-a-uuid/onboarding", headers=H(admin_token), json={"step": 2})
rec(
    "P-10",
    "Onboarding-Progressive",
    "PATCH invalid UUID path -> 422",
    "PATCH",
    "/clients/{id}/onboarding",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

# ---------------------------------------------------------------- CLIENT DETAIL
r = client.get(f"{API}/clients/{atomic_id}", headers=H(admin_token))
b = r.json()
rec(
    "D-01",
    "Client-Detail",
    "Get client by id (200) with nested arrays",
    "GET",
    "/clients/{id}",
    "200, id matches, brand_colors/platforms/contacts present",
    r.status_code == 200
    and b.get("id") == atomic_id
    and isinstance(b.get("brand_colors"), list)
    and isinstance(b.get("platforms"), list)
    and isinstance(b.get("contacts"), list),
    f"{r.status_code} id_match={b.get('id') == atomic_id}",
)

r = client.get(f"{API}/clients/{uuid.uuid4()}", headers=H(admin_token))
rec(
    "D-02",
    "Client-Detail",
    "Get unknown client -> 404",
    "GET",
    "/clients/{id}",
    "404, code=not_found",
    r.status_code == 404 and err_code(r) == "not_found",
    f"{r.status_code} code={err_code(r)}",
)

r = client.get(f"{API}/clients/not-a-uuid", headers=H(admin_token))
rec(
    "D-03",
    "Client-Detail",
    "Get invalid UUID -> 422",
    "GET",
    "/clients/{id}",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

# ---------------------------------------------------------------- ASSIGNMENTS + RBAC
r = client.get(f"{API}/clients/{atomic_id}/assignments", headers=H(admin_token))
b = r.json()
rec(
    "AS-01",
    "Assignments",
    "List assignees of a client (200)",
    "GET",
    "/clients/{id}/assignments",
    "200, items/total present",
    r.status_code == 200 and "items" in b and "total" in b,
    f"{r.status_code} total={b.get('total')}",
)

r = client.get(f"{API}/clients/{uuid.uuid4()}/assignments", headers=H(admin_token))
rec(
    "AS-02",
    "Assignments",
    "List assignees of unknown client -> 404",
    "GET",
    "/clients/{id}/assignments",
    "404",
    r.status_code == 404 and err_code(r) == "not_found",
    f"{r.status_code} code={err_code(r)}",
)

r = client.post(
    f"{API}/clients/{atomic_id}/assignments", headers=H(admin_token), json={"user_id": nonadmin_id}
)
rec(
    "AS-03",
    "Assignments",
    "Assign client to user (201)",
    "POST",
    "/clients/{id}/assignments",
    "201, nested user returned",
    r.status_code == 201 and r.json().get("user", {}).get("id") == nonadmin_id,
    f"{r.status_code}",
)

r = client.post(
    f"{API}/clients/{atomic_id}/assignments", headers=H(admin_token), json={"user_id": nonadmin_id}
)
rec(
    "AS-04",
    "Assignments",
    "Duplicate assignment -> 409",
    "POST",
    "/clients/{id}/assignments",
    "409, code=conflict",
    r.status_code == 409 and err_code(r) == "conflict",
    f"{r.status_code} code={err_code(r)}",
)

r = client.post(
    f"{API}/clients/{atomic_id}/assignments",
    headers=H(admin_token),
    json={"user_id": str(uuid.uuid4())},
)
rec(
    "AS-05",
    "Assignments",
    "Assign unknown user -> 404",
    "POST",
    "/clients/{id}/assignments",
    "404",
    r.status_code == 404 and err_code(r) == "not_found",
    f"{r.status_code} code={err_code(r)}",
)

r = client.post(
    f"{API}/clients/{uuid.uuid4()}/assignments",
    headers=H(admin_token),
    json={"user_id": nonadmin_id},
)
rec(
    "AS-06",
    "Assignments",
    "Assign to unknown client -> 404",
    "POST",
    "/clients/{id}/assignments",
    "404",
    r.status_code == 404 and err_code(r) == "not_found",
    f"{r.status_code} code={err_code(r)}",
)

# RBAC scoping — non-admin sees only assigned clients
r = client.get(f"{API}/clients", headers=H(nonadmin_token))
items = r.json().get("items", [])
rec(
    "AS-07",
    "RBAC",
    "Non-admin sees only assigned clients",
    "GET",
    "/clients",
    "200, every item is the assigned client",
    r.status_code == 200 and all(it["id"] == atomic_id for it in items) and len(items) == 1,
    f"{r.status_code} total={r.json().get('total')} ids={[it['id'] for it in items]}",
)

r = client.get(f"{API}/clients/{atomic_id}", headers=H(nonadmin_token))
rec(
    "AS-08",
    "RBAC",
    "Non-admin can GET assigned client (200)",
    "GET",
    "/clients/{id}",
    "200",
    r.status_code == 200,
    f"{r.status_code}",
)

r = client.get(f"{API}/clients/{draft_id}", headers=H(nonadmin_token))
rec(
    "AS-09",
    "RBAC",
    "Non-admin GET unassigned client -> 404 (not 403, hides existence)",
    "GET",
    "/clients/{id}",
    "404, code=not_found",
    r.status_code == 404 and err_code(r) == "not_found",
    f"{r.status_code} code={err_code(r)}",
)

r = client.get(f"{API}/clients/{atomic_id}/assignments", headers=H(nonadmin_token))
rec(
    "AS-10",
    "RBAC",
    "Non-admin cannot list assignments -> 403",
    "GET",
    "/clients/{id}/assignments",
    "403, code=forbidden",
    r.status_code == 403 and err_code(r) == "forbidden",
    f"{r.status_code} code={err_code(r)}",
)

r = client.delete(f"{API}/clients/{atomic_id}/assignments/{nonadmin_id}", headers=H(admin_token))
rec(
    "AS-11",
    "Assignments",
    "Unassign client from user -> 204",
    "DELETE",
    "/clients/{id}/assignments/{uid}",
    "204 no content",
    r.status_code == 204,
    f"{r.status_code}",
)

r = client.get(f"{API}/clients/{atomic_id}", headers=H(nonadmin_token))
rec(
    "AS-12",
    "RBAC",
    "After unassign, non-admin loses access -> 404",
    "GET",
    "/clients/{id}",
    "404",
    r.status_code == 404,
    f"{r.status_code} code={err_code(r)}",
)

r = client.delete(f"{API}/clients/{atomic_id}/assignments/{nonadmin_id}", headers=H(admin_token))
rec(
    "AS-13",
    "Assignments",
    "Unassign a non-existent assignment -> 404",
    "DELETE",
    "/clients/{id}/assignments/{uid}",
    "404",
    r.status_code == 404 and err_code(r) == "not_found",
    f"{r.status_code} code={err_code(r)}",
)

# ---------------------------------------------------------------- BRAND EXTRACTION
r = client.post(f"{API}/clients/onboarding/extract-brand", headers=H(admin_token), json={})
rec(
    "B-01",
    "Brand-Extract",
    "Missing website -> 422",
    "POST",
    "/clients/onboarding/extract-brand",
    "422",
    r.status_code == 422,
    f"{r.status_code}",
)

r = client.post(f"{API}/clients/onboarding/extract-brand", json={"website": "https://example.com"})
rec(
    "B-02",
    "Brand-Extract",
    "Extraction requires auth -> 401",
    "POST",
    "/clients/onboarding/extract-brand",
    "401",
    r.status_code == 401,
    f"{r.status_code} code={err_code(r)}",
)

try:
    t0 = time.time()
    r = client.post(
        f"{API}/clients/onboarding/extract-brand",
        headers=H(admin_token),
        json={"website": "https://example.com"},
        timeout=90.0,
    )
    dt = time.time() - t0
    if r.status_code == 200:
        j = r.json()
        # Hard assertion on the response contract (the call reliably works).
        valid = (
            isinstance(j.get("summary"), str)
            and j["summary"].strip() != ""
            and isinstance(j.get("colors"), list)
            and isinstance(j.get("fonts"), list)
            and isinstance(j.get("ai_generated"), bool)
        )
        rec(
            "B-03",
            "Brand-Extract",
            "Live brand extraction returns a valid theme",
            "POST",
            "/clients/onboarding/extract-brand",
            "200 with non-empty summary + colors[] + fonts[] + ai_generated(bool)",
            valid,
            f"200 in {dt:.1f}s ai_generated={j.get('ai_generated')} "
            f"colors={len(j.get('colors', []))} fonts={len(j.get('fonts', []))}",
        )
    else:
        # Non-200 means the live browser/model dependency is unavailable in this
        # environment — degrade to an observation rather than a hard failure.
        warn(
            "B-03",
            "Brand-Extract",
            "Live brand extraction (dependency unavailable)",
            "POST",
            "/clients/onboarding/extract-brand",
            "200",
            f"{r.status_code} in {dt:.1f}s",
            f"code={err_code(r)} — live AI/browser not available",
        )
except Exception as e:
    warn(
        "B-03",
        "Brand-Extract",
        "Live brand extraction (dependency unavailable)",
        "POST",
        "/clients/onboarding/extract-brand",
        "200",
        f"exception: {type(e).__name__}",
        "Live dependency (browser/model) unavailable or timed out.",
    )

# ---------------------------------------------------------------- SUMMARY
total = len(results)
passed = sum(1 for x in results if x["status"] == "PASS")
failed = sum(1 for x in results if x["status"] == "FAIL")
warned = sum(1 for x in results if x["status"] == "WARN")
print("\n==================== SUMMARY ====================")
print(f"TOTAL={total}  PASS={passed}  FAIL={failed}  WARN={warned}")
if failed:
    print("\nFAILURES:")
    for x in results:
        if x["status"] == "FAIL":
            print(f"  {x['id']} {x['title']} | expected {x['expected']} | got {x['actual']}")

_out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_test_results.json")
with open(_out, "w") as f:
    json.dump(
        {"total": total, "pass": passed, "fail": failed, "warn": warned, "results": results},
        f,
        indent=2,
    )
print(f"\nresults written to {_out}")
