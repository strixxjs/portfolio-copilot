SHELL := /bin/bash
.DEFAULT_GOAL := help

# PY runs Python inside the api container by default. Override for local or CI runs: make test PY="uv run"
PY ?= docker compose exec -T api uv run
COMPOSE ?= docker compose
USER_EMAIL ?= carol@example.test
Q ?= What is the status of the largest approved change order on P1?

.PHONY: help up down logs migrate seed dev test test-unit test-integration lint fmt typecheck \
        eval eval-offline report ask mcp tf-validate ci

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

up: ## build and start db + api
	$(COMPOSE) up -d --build

down: ## stop everything and drop volumes
	$(COMPOSE) down -v

logs: ## tail api logs
	$(COMPOSE) logs -f api

migrate: ## apply db/migrations once each (admin role)
	$(PY) python scripts/migrate.py

seed: migrate ## generate the synthetic corpus, embed on CPU, load everything
	$(PY) python scripts/seed.py

dev: ## run the API locally with reload (db from compose)
	uv run uvicorn app.main:app --reload --port 8000

test: ## all tests (unit + integration; live tests skip without GROQ_API_KEY)
	$(PY) pytest -q

test-unit: ## unit tests only, no database
	$(PY) pytest -q tests/unit

test-integration: ## integration tests, needs the database
	$(PY) pytest -q tests/integration -m integration

lint: ## ruff check + format check
	$(PY) ruff check .
	$(PY) ruff format --check .

fmt: ## ruff format + autofix
	$(PY) ruff format .
	$(PY) ruff check --fix .

typecheck: ## mypy strict
	$(PY) mypy

eval: ## live eval against the configured provider (writes eval/results/)
	$(PY) python eval/run_eval.py --mode live --out eval/results $(EVAL_ARGS)

eval-offline: ## offline eval with the ScriptedProvider (no API key; CI gate)
	$(PY) python eval/run_eval.py --mode offline --out eval/results $(EVAL_ARGS)

report: ## latency / cost / cache report from traces
	$(PY) python scripts/report.py $(REPORT_ARGS)

ask: ## stream one question: make ask Q="..." USER_EMAIL=alice@example.test
	$(PY) python scripts/ask.py --user "$(USER_EMAIL)" "$(Q)"

mcp: ## run the MCP server on stdio as MCP_USER_EMAIL
	$(PY) python -m app.mcp_server

tf-validate: ## terraform fmt + validate (no cloud credentials needed)
	cd infra/terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false >/dev/null && terraform validate

ci: lint typecheck test eval-offline ## what CI runs
