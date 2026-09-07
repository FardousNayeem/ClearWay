BACKEND := backend
PY      := $(BACKEND)/.venv/bin/python
WEB     := web

.PHONY: help db setup migrate drift bootstrap api web scheduler test lint check up down clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

db:  ## Start the development database on 5436
	docker run -d --name clearway-postgres --rm \
	  -e POSTGRES_USER=clearway -e POSTGRES_PASSWORD=clearway -e POSTGRES_DB=clearway \
	  -p 127.0.0.1:5436:5432 postgres:17-alpine

setup:  ## Create both toolchains
	cd $(BACKEND) && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt
	cd $(WEB) && npm install
	cp -n $(BACKEND)/.env.example $(BACKEND)/.env || true
	cp -n $(WEB)/.env.local.example $(WEB)/.env.local || true

migrate:  ## Apply database migrations
	cd $(BACKEND) && .venv/bin/alembic upgrade head

drift:  ## Prove no model change is missing a migration
	cd $(BACKEND) && .venv/bin/alembic upgrade head
	@cd $(BACKEND) && .venv/bin/alembic revision --autogenerate \
	  -m "drift check" --rev-id drift_check >/dev/null && \
	  if grep -qE "op\.(create|drop|add|alter)" migrations/versions/*drift_check*.py; then \
	    echo "Model changes are not captured in a migration:"; \
	    cat migrations/versions/*drift_check*.py; \
	    rm -f migrations/versions/*drift_check*.py; exit 1; \
	  else \
	    rm -f migrations/versions/*drift_check*.py; echo "schema matches the models"; \
	  fi

bootstrap:  ## Discover stations, backfill, train, forecast
	$(PY) -m app.cli bootstrap --days 60

api:  ## Run the API on :8000
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port 8000

web:  ## Run the site on :3000
	cd $(WEB) && npm run dev

scheduler:  ## Run the background jobs
	cd $(BACKEND) && .venv/bin/python -m app.jobs.run

status:  ## Data coverage per station
	cd $(BACKEND) && .venv/bin/python -m app.cli status

test:  ## Backend tests with coverage
	cd $(BACKEND) && .venv/bin/python -m pytest --cov=app --cov-report=term-missing

lint:  ## Lint and type-check both halves
	cd $(BACKEND) && .venv/bin/ruff check . && .venv/bin/mypy app
	cd $(WEB) && npm run typecheck && npm run lint

check: lint drift test  ## Lint, type-check, prove the schema, and test

up:  ## Bring the stack up in Docker
	docker compose up --build -d

down:  ## Tear the stack down
	docker compose down

clean:  ## Remove build artefacts
	rm -rf $(WEB)/.next $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache
