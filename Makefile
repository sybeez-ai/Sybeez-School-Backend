.PHONY: up down logs api ai worker migrate revision seed test fmt deps

up:
	docker compose -f docker-compose.dev.yml up -d postgres redis

down:
	docker compose -f docker-compose.dev.yml down

logs:
	docker compose -f docker-compose.dev.yml logs -f

deps:
	cd apps/api && .venv/bin/pip install -q -e ".[dev]"

api: up
	cd apps/api && .venv/bin/pip install -q -e ".[dev]" && \
	PYTHONPATH=. .venv/bin/uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

ai:
	cd apps/ai && uvicorn src.main:app --reload --host 0.0.0.0 --port 8001

worker:
	cd apps/worker && python -m src.main

migrate:
	cd apps/api && alembic -c alembic.ini upgrade head

revision:
	cd apps/api && alembic -c alembic.ini revision --autogenerate -m "$(m)"

seed:
	cd apps/api && python -m src.db.seed

test:
	cd apps/api && pytest -q

fmt:
	ruff check --fix apps packages || true
