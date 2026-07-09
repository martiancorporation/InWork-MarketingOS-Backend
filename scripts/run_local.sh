#!/usr/bin/env bash
#
# One-command local bootstrap + run.
#
#   ./scripts/run_local.sh
#
# Idempotent: safe to run repeatedly. It will
#   1. find a suitable Python (3.11+) and create a virtualenv
#   2. install dependencies (only when requirements change)
#   3. create .env.local from the template with a freshly generated SECRET_KEY
#   4. start a local Postgres (Docker) and wait until it's ready
#   5. generate the initial migration if needed, then apply all migrations
#   6. start the FastAPI dev server with autoreload
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Locate the backend root (this script lives in Backend/scripts/).
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

export APP_ENV=local
VENV_DIR=".venv"
VENV_PY="$VENV_DIR/bin/python"
REQ_STAMP="$VENV_DIR/.requirements.installed"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

log()  { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m  ! %s\033[0m\n" "$*"; }
die()  { printf "\n\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Python + virtualenv
# ---------------------------------------------------------------------------
choose_python() {
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
        echo "$candidate"; return 0
      fi
    fi
  done
  return 1
}

log "Checking Python (3.11+ required)"
if [ ! -x "$VENV_PY" ]; then
  PYTHON_BIN="$(choose_python)" || die "Python 3.11+ not found. Install it and re-run."
  ok "Using $($PYTHON_BIN --version) at $(command -v "$PYTHON_BIN")"
  log "Creating virtualenv in $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  ok "virtualenv created"
else
  ok "virtualenv already present ($("$VENV_PY" --version))"
fi

# ---------------------------------------------------------------------------
# 2. Dependencies (skip if requirements.txt hasn't changed)
# ---------------------------------------------------------------------------
if [ ! -f "$REQ_STAMP" ] || [ requirements.txt -nt "$REQ_STAMP" ]; then
  log "Installing dependencies"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install --quiet -r requirements.txt
  touch "$REQ_STAMP"
  ok "dependencies installed"
else
  ok "dependencies up to date"
fi

# ---------------------------------------------------------------------------
# 3. .env.local (created once, with a generated SECRET_KEY)
# ---------------------------------------------------------------------------
if [ ! -f ".env.local" ]; then
  log "Creating .env.local from template"
  cp .env.local.example .env.local
  "$VENV_PY" - <<'PY'
import pathlib, re, secrets
p = pathlib.Path(".env.local")
key = secrets.token_urlsafe(48)
lines, replaced = [], False
for line in p.read_text().splitlines():
    if re.match(r"^\s*SECRET_KEY\s*=", line):
        lines.append(f"SECRET_KEY={key}"); replaced = True
    else:
        lines.append(line)
if not replaced:
    lines.append(f"SECRET_KEY={key}")
p.write_text("\n".join(lines) + "\n")
PY
  ok ".env.local created with a generated SECRET_KEY"
else
  ok ".env.local already exists (left untouched)"
fi

# ---------------------------------------------------------------------------
# 4. Database (Docker Postgres) + wait for readiness
# ---------------------------------------------------------------------------
docker_ready() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@";
  elif command -v docker-compose >/dev/null 2>&1; then docker-compose "$@";
  else return 1; fi
}

wait_for_db() {
  "$VENV_PY" - <<'PY'
import sys, time
sys.path.insert(0, ".")
from sqlalchemy import text
from app.db.session import get_engine
for _ in range(30):
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        sys.exit(0)
    except Exception:
        time.sleep(1)
sys.exit(1)
PY
}

log "Starting the database"
if docker_ready; then
  compose up -d db && ok "Postgres container started" || die "Failed to start Postgres via docker compose"
else
  warn "Docker not available — will use an existing Postgres at your DATABASE_URL"
fi

printf "  waiting for the database"
if wait_for_db; then printf "\n"; ok "database is ready"; else
  printf "\n"
  die "Database not reachable. Start Docker (then re-run) or point DATABASE_URL in .env.local at a running Postgres."
fi

# ---------------------------------------------------------------------------
# 5. Migrations
# ---------------------------------------------------------------------------
if ! find migrations/versions -maxdepth 1 -name '*.py' 2>/dev/null | grep -q .; then
  log "Generating the initial migration"
  "$VENV_DIR/bin/alembic" revision --autogenerate -m "initial schema"
  ok "initial migration created"
fi
log "Applying migrations"
"$VENV_DIR/bin/alembic" upgrade head
ok "database schema is up to date"

# ---------------------------------------------------------------------------
# 6. Run the server
# ---------------------------------------------------------------------------
log "Starting the API on http://$HOST:$PORT  (docs: http://localhost:$PORT/docs)"
exec "$VENV_DIR/bin/uvicorn" app.main:app --reload --host "$HOST" --port "$PORT"
