# scripts/

Operational and developer convenience scripts that are **not** part of the
running application (startup wrappers, database seeding, one-off maintenance).

Keep each script single-purpose and runnable on its own. Nothing here should be
imported by the app package.
