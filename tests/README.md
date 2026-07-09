# tests/

Automated tests for the backend.

- `unit/` — fast, isolated tests for a single function/service (no DB, no network).
- `integration/` — tests that exercise multiple layers (API + services + DB).
- `fixtures/` — shared sample data and pytest fixtures.

`conftest.py` holds fixtures shared across the whole suite. Mirror the `app/`
package layout inside each test folder so tests are easy to locate.
