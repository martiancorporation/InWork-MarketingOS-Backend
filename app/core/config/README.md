# app/core/config/

The single source of truth for configuration. Settings are defined with
`pydantic-settings` and populated from environment variables / a local `.env`
file. **No real secret values are ever committed** — only the variable names,
documented in the root `.env.example`.

Split by concern so no single settings file grows large:
- `env.py` — selects the environment (`APP_ENV`) and builds the dotenv file list.
- `base.py` — the composed `Settings` object + `get_settings()` accessor.
- `app_settings.py` — server/app-level settings (name, env, debug, prefixes).
- `database.py` — database connection settings (`DATABASE_*`).
- `security.py` — auth/JWT/CORS settings.
- `ai.py` — Anthropic model + key settings (`ANTHROPIC_*`).
- `integrations.py` — OAuth client ids/secrets for Google, Meta, LinkedIn.

## Environments
`APP_ENV` (`local` | `development` | `production`, default `local`) selects the
environment. Config is layered, lowest priority first:

1. `.env` — shared, non-secret defaults (optional)
2. `.env.{APP_ENV}` — environment-specific values (overrides `.env`)
3. real OS environment variables — always win (how production injects secrets)

Switch environments by setting one variable: `APP_ENV=production`. Templates for
each environment live at the repo root as `.env.{env}.example`.

Access config through `get_settings()` — never call `os.environ` elsewhere
(except `env.py`, which only reads the `APP_ENV` selector).
