# AI Tech Consultant: one entry point for the three tasks.
# Every target is a thin wrapper around the per-task commands, so CI and
# your laptop run exactly the same thing. `make help` lists them all.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Knobs: make crawl LIMIT=100 | make report SPLIT=heldout
#        make eval BRAIN_EVAL_ARGS="--mock" | CI uses NPM_INSTALL=ci
LIMIT ?= 100
SPLIT ?=
BRAIN_EVAL_ARGS ?=
NPM_INSTALL ?= install
HONCHO := uvx honcho@2.0.0

# Load .env and export it to every command. Values in .env win over the shell.
# The first `make` of any kind creates .env from .env.example (rule below).
-include .env
export

# Tests must never call paid APIs, even if keys sit in .env.
OFFLINE := env -u ANTHROPIC_API_KEY -u VOYAGE_API_KEY -u GEMINI_API_KEY -u NVIDIA_API_KEY

# $(call in,<folder>,<command>): run a command inside a task folder.
# Skips with a notice when the folder is not on this branch yet.
in = if [ -d $(1) ]; then cd $(1) && $(2); else echo "skip: $(1)/ is not on this branch yet"; fi

.PHONY: help setup setup-brain setup-scope setup-inbox up down dev migrate \
	test test-brain test-scope test-inbox test-report \
	typecheck typecheck-brain typecheck-scope typecheck-inbox \
	lint lint-brain lint-scope lint-inbox lint-report build-inbox \
	seed crawl sync eval eval-brain eval-inbox eval-scope report

help: ## Show this list
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.env:
	cp .env.example .env
	@echo "Created .env from .env.example. Add ANTHROPIC_API_KEY and VOYAGE_API_KEY for real model calls."

# ---- run -------------------------------------------------------------------

up: .env ## Run the whole project in Docker (db, brain, scheduler, inbox, scope)
	docker compose up --build

down: ## Stop Docker services (data volumes are kept)
	docker compose down

dev: .env migrate ## Run all three locally without Docker (needs local Postgres); Ctrl-C stops all
	$(HONCHO) -f Procfile.dev start

setup: .env setup-brain setup-scope setup-inbox ## Install dependencies for all three tasks

setup-brain:
	@$(call in,brain,uv sync)
setup-scope:
	@$(call in,scope,uv sync)
setup-inbox:
	@$(call in,inbox,npm $(NPM_INSTALL))

migrate: ## Create or upgrade the Knowledge Brain database schema
	@$(call in,brain,uv run python -m brain.migrate)

seed: ## Load demo emails into the Inbox Agent
	@$(call in,inbox,npm run seed)

crawl: ## Crawl the help center into the Brain (make crawl LIMIT=100)
	@$(call in,brain,uv run python -m brain.crawl --limit $(LIMIT))

sync: ## Run one incremental help-center sync now
	@$(call in,brain,uv run python -m brain.sync)

# ---- quality ---------------------------------------------------------------

test: test-brain test-scope test-inbox test-report ## Run all test suites (offline, no API keys)

test-brain:
	@$(call in,brain,$(OFFLINE) uv run pytest)
test-scope:
	@$(call in,scope,$(OFFLINE) uv run pytest)
test-inbox:
	@$(call in,inbox,$(OFFLINE) npm test)
test-report:
	python3 evals/test_report.py

typecheck: typecheck-brain typecheck-scope typecheck-inbox ## Type-check all three tasks (mypy, tsc)

typecheck-brain:
	@$(call in,brain,uv run mypy src)
typecheck-scope:
	@$(call in,scope,uv run mypy src)
typecheck-inbox:
	@$(call in,inbox,npm run typecheck)

lint: lint-brain lint-scope lint-inbox lint-report ## Lint all three tasks (ruff, eslint)

lint-brain:
	@$(call in,brain,uv run ruff check .)
lint-scope:
	@$(call in,scope,uv run ruff check .)
lint-inbox:
	@$(call in,inbox,npm run lint)
lint-report:
	uvx ruff@0.17.1 check evals

build-inbox:
	@$(call in,inbox,npm run build)

# ---- evals -----------------------------------------------------------------

eval: eval-brain eval-inbox eval-scope report ## Run all three eval runners, then write the report

eval-brain:
	@$(call in,brain,uv run python -m brain.evals.run $(BRAIN_EVAL_ARGS))
eval-inbox:
	@$(call in,inbox,npm run eval)
eval-scope:
	@$(call in,scope,uv run python -m scope.evals.run)

report: ## Write evals/results/summary.md (make report SPLIT=heldout)
	python3 evals/report.py $(if $(SPLIT),--split $(SPLIT))
