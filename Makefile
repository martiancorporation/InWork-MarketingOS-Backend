# Developer commands. Override the environment per invocation, e.g.:
#     make run APP_ENV=local
#     make migrate APP_ENV=development
#
# APP_ENV selects which .env.<env> file the app loads (default: local).
APP_ENV ?= local
export APP_ENV

HOST ?= 0.0.0.0
PORT ?= 8000

# Auto-use the project virtualenv's tools when .venv exists, so `make` works
# without activating it first. Falls back to PATH otherwise.
VENV_BIN := .venv/bin
PYTHON  := $(if $(wildcard $(VENV_BIN)/python),$(VENV_BIN)/python,python3)
ALEMBIC := $(if $(wildcard $(VENV_BIN)/alembic),$(VENV_BIN)/alembic,alembic)
UVICORN := $(if $(wildcard $(VENV_BIN)/uvicorn),$(VENV_BIN)/uvicorn,uvicorn)

# Pick a Python 3.11+ interpreter to build the venv with.
BOOTSTRAP_PYTHON := $(shell for p in python3.13 python3.12 python3.11 python3; do command -v $$p >/dev/null 2>&1 && { "$$p" -c 'import sys;exit(0 if sys.version_info[:2]>=(3,11) else 1)' 2>/dev/null && echo $$p && break; }; done)

.PHONY: help start venv install migration migrate downgrade seed run run-prod db-up db-down test lint format

help:  ## list available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

start:  ## one command: set up everything and run locally
	./scripts/run_local.sh

venv:  ## create the .venv virtualenv (Python 3.11+)
	@test -n "$(BOOTSTRAP_PYTHON)" || { echo "Python 3.11+ not found"; exit 1; }
	$(BOOTSTRAP_PYTHON) -m venv .venv
	@echo "Created .venv using $(BOOTSTRAP_PYTHON)"

install: venv  ## create the venv (if needed) and install dependencies
	$(VENV_BIN)/python -m pip install --upgrade pip
	$(VENV_BIN)/python -m pip install -r requirements.txt

migration:  ## create a migration: make migration m="add users table"
	$(ALEMBIC) revision --autogenerate -m "$(m)"

migrate:  ## apply all pending migrations
	$(ALEMBIC) upgrade head

downgrade:  ## roll back the last migration
	$(ALEMBIC) downgrade -1

seed:  ## create the initial admin user (idempotent)
	$(PYTHON) scripts/seed_data.py

run:  ## run the dev server with autoreload
	$(UVICORN) app.main:app --reload --host $(HOST) --port $(PORT)

run-prod:  ## run without reload, multiple workers (APP_ENV should be production)
	$(UVICORN) app.main:app --host $(HOST) --port $(PORT) --workers 4

db-up:  ## start the local Postgres container
	docker compose up -d db

db-down:  ## stop the local Postgres container
	docker compose down

test:  ## run the test suite
	$(PYTHON) -m pytest -q

lint:  ## check formatting/lint (if ruff is installed)
	$(PYTHON) -m ruff check .

format:  ## auto-format (if ruff is installed)
	$(PYTHON) -m ruff format .
