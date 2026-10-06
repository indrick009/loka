.PHONY: help up down logs migrate revision api worker test lint typecheck fmt shell psql

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-14s\033[0m %s\n", $$1, $$2}'

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

migrate:
	docker compose run --rm migrate

revision:
	docker compose run --rm migrate alembic revision --autogenerate -m "$(m)"

api:
	docker compose up -d api

worker:
	docker compose up -d worker

test:
	docker compose --profile test run --rm test

test-all:
	docker compose --profile test run --rm test pytest

lint:
	docker compose --profile test run --rm --no-deps test ruff check src tests

fmt:
	docker compose --profile test run --rm --no-deps test ruff format src tests

typecheck:
	docker compose --profile test run --rm --no-deps test mypy

psql:
	docker compose exec postgres psql -U $${POSTGRES_USER:-loka} -d $${POSTGRES_DB:-loka}

shell:
	docker compose run --rm api bash