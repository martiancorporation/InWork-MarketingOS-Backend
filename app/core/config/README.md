# app/core/config/

The single source of truth for configuration. Settings are defined with
`pydantic-settings` and populated from environment variables / a local `.env`
file. **No real secret values are ever committed** — only the variable names,
documented in the root `.env.example`.

Split by concern so no single settings file grows large:
- `base.py` — the composed `Settings` object + `get_settings()` accessor.
- `app_settings.py` — server/app-level settings (name, env, debug, prefixes).
- `database.py` — database connection settings.
- `security.py` — auth/JWT/CORS settings.
- `ai.py` — Anthropic model + key settings.
- `integrations.py` — OAuth client ids/secrets for Google, Meta, LinkedIn.

Access config through `get_settings()` — never call `os.environ` elsewhere.
