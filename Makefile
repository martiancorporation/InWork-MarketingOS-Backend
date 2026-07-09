# Developer commands. Override the environment per invocation, e.g.:
#     make run APP_ENV=local
#     make migrate APP_ENV=development
#
# APP_ENV selects which .env.<env> file the app loads (default: local).
APP_ENV ?= local
export APP_ENV

PYTHON ?= python
HOST ?= 0.0.0.0
PORT ?= 8000

.PHONY: help venv install migration migrate downgrade run run-prod db-up db-down test lint format

help:  ## list available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

venv:  ## create a local virtualenv in .venv
	$(PYTHON) -m venv .venv
	@echo "Activate it with: source .venv/bin/activate"

install:  ## install dependencies
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

migration:  ## create a migration: make migration m="add users table"
	alembic revision --autogenerate -m "$(m)"

migrate:  ## apply all pending migrations
	alembic upgrade head

downgrade:  ## roll back the last migration
	alembic downgrade -1

run:  ## run the dev server with autoreload
	uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

run-prod:  ## run without reload, multiple workers (APP_ENV should be production)
	uvicorn app.main:app --host $(HOST) --port $(PORT) --workers 4

db-up:  ## start the local Postgres container
	docker compose up -d db

db-down:  ## stop the local Postgres container
	docker compose down

test:  ## run the test suite
	pytest -q

lint:  ## check formatting/lint (if ruff is installed)
	ruff check .

format:  ## auto-format (if ruff is installed)
	ruff format .
