"""Environment selection & the dotenv file layering strategy.

One variable, ``APP_ENV`` (``local`` | ``development`` | ``production``),
selects the environment. Configuration is then layered, lowest priority first:

    1. ``.env``                 — shared, non-secret defaults (optional)
    2. ``.env.{APP_ENV}``       — environment-specific values (overrides .env)
    3. real OS environment vars — always win (how production injects secrets)

``APP_ENV`` itself is read straight from the OS environment here (it only
*selects* config; it is not a secret) and defaults to ``local``.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root: .../Backend/app/core/config/env.py -> parents[3] == .../Backend
BASE_DIR = Path(__file__).resolve().parents[3]

APP_ENV = os.getenv("APP_ENV", "local").strip().lower()

# Absolute paths so config loads regardless of the current working directory.
# Missing files are ignored by pydantic-settings, so listing both is safe.
ENV_FILES: tuple[str, ...] = (
    str(BASE_DIR / ".env"),
    str(BASE_DIR / f".env.{APP_ENV}"),
)
