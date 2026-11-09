.PHONY: setup install db-up db-down db-local-up db-local-down test test-unit test-db eval viewer

# Linux/macOS. On Windows use scripts\setup.ps1 (same steps).
# Every target that touches the database reads .env (written by `make setup`).
LOAD_ENV = set -a; . ./.env; set +a

# --- one command: venv, packages, .env, Postgres, incidents ---
setup:
	python3 -m venv .venv
	.venv/bin/pip install -q -e ".[dev,api]"
	.venv/bin/python scripts/make_env.py
	docker compose up -d --wait db
	$(LOAD_ENV); .venv/bin/python -m investigator.generator

install:
	pip install -e ".[dev,api]"

# --- PostgreSQL via Docker (normal path) ---
db-up:
	docker compose up -d --wait db

db-down:
	docker compose down

# --- PostgreSQL without Docker (throwaway local cluster, tests only) ---
db-local-up:
	bash scripts/start_local_pg.sh

db-local-down:
	bash scripts/stop_local_pg.sh

# --- tests ---
# Unit tests need nothing. Tests marked "db" create and drop their own
# database called incident_test on whatever server IV_TEST_PG_* points to.
test-unit:
	.venv/bin/python -m pytest tests/unit

test:
	pytest

test-db:
	pytest -m db

# --- evaluation (spends Claude quota; resumable) and the read-only viewer ---
eval:
	$(LOAD_ENV); .venv/bin/python scripts/run_eval.py --label eval-v4 --pattern exchange --company-context --cache-mode off

viewer:
	$(LOAD_ENV); .venv/bin/python -m investigator.web --label eval-v4
