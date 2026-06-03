# Makefile — AtlasCore
#
# Developer entrypoints (uv-managed). These wrap the toolchain and the local
# stack. The OPERATIONAL surface is the `atlas` CLI (the only ingestion-
# management surface; agents never use it). Rough mapping of make -> atlas:
#
#   make dev        -> brings up Postgres 16 + pgvector (+ optional Meilisearch)
#   make migrate    -> alembic upgrade head      (atlas has no migrate; CLI is for ops)
#   make seed       -> register fixture/demo sources (cf. `atlas source validate/test`)
#   make smoke      -> end-to-end: atlas crawl once -> parse -> search -> MCP -> alert
#
#   Operator commands (run after `make dev` + `make migrate`):
#     atlas source validate path/to/source.yaml
#     atlas source test <source_id>
#     atlas crawl once <source_id>
#     atlas parse <raw_id>     /  atlas enrich <doc_id>
#     atlas health             /  atlas digest      (CLI-driven, MCP-independent)
#     atlas alerts run         /  atlas mcp dev
#     atlas backup             /  atlas purge <doc_id|entity_id>
#
# NOTE: the exact `atlas` module path / import layout is finalized in MVP block B1.

# Use uv for everything; fall back to a bare invocation if a target predates B1.
UV ?= uv
RUN := $(UV) run
COMPOSE ?= docker compose -f infra/docker-compose.yml

.DEFAULT_GOAL := help
.PHONY: help setup dev down migrate seed format lint typecheck test \
        test-unit test-integration smoke audit precommit clean

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Install deps (dev extra) and init local env (.env from .env.example).
	$(UV) venv
	$(UV) sync --extra dev
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — fill in real values (gitignored).")
	$(RUN) pre-commit install
	@echo "Setup complete. Next: make dev && make migrate && make seed"

dev: ## Run the local stack: Postgres 16 + pgvector (+ optional Meilisearch).
	$(COMPOSE) up -d
	@echo "Stack up. Postgres 16 + pgvector ready (pg_trgm enabled by the first migration)."

down: ## Stop the local stack (keeps volumes).
	$(COMPOSE) down

migrate: ## Apply Alembic migrations (postgres only).
	$(RUN) alembic upgrade head

seed: ## Validate + register the source registry (needs `make dev && make migrate`).
	$(RUN) atlas source validate --all
	$(RUN) atlas source sync --all
	$(RUN) atlas source list

format: ## ruff format (the only formatter).
	$(RUN) ruff format .

lint: ## ruff lint.
	$(RUN) ruff check .

typecheck: ## mypy static typing.
	$(RUN) mypy

test: ## Unit + contract + integration (no live network in unit). Mirrors CI.
	$(RUN) pytest

test-unit: ## Fast: unit + contract only (no DB, no live network).
	$(RUN) pytest -m "unit or contract"

test-integration: ## DB / raw-store / search / queue paths (needs `make dev`).
	$(RUN) pytest -m integration

smoke: ## Local end-to-end smoke: source -> ingest -> search -> MCP summary -> alert.
	$(RUN) pytest -m smoke

audit: ## Dependency vulnerability audit (pip-audit).
	$(RUN) pip-audit

precommit: ## Run all pre-commit hooks on the whole tree (what CI also runs).
	$(RUN) pre-commit run --all-files

clean: ## Remove caches and build artifacts (NOT .venv, NOT the raw store, NOT .env).
	rm -rf .ruff_cache .mypy_cache .pytest_cache .coverage htmlcov dist build
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +
