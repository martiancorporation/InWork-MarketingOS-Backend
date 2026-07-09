# Common developer commands (filled in during implementation).
.PHONY: install run test lint format migrate

install:   ## install dependencies
	@echo "TODO: install dependencies"

run:       ## run the dev server
	@echo "TODO: uvicorn app.main:app --reload"

test:      ## run the test suite
	@echo "TODO: pytest"

lint:      ## run linters
	@echo "TODO: ruff check ."

format:    ## auto-format
	@echo "TODO: ruff format ."

migrate:   ## apply database migrations
	@echo "TODO: alembic upgrade head"
