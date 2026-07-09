# app/services/

The business-logic layer — the heart of the backend. One file per domain
(`client_service.py`, `readiness_service.py`, …).

Services orchestrate the work: they call repositories for data, the `ai/` layer
for intelligence, and `integrations/` for external systems, then apply the
rules. They must not contain raw SQL or HTTP request handling.
