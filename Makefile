.DEFAULT_GOAL := help
SHELL := /bin/bash

UV ?= uv
COMPOSE ?= docker compose

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Install the project with dev + provider extras (uv)
	$(UV) sync --extra dev --extra providers --extra redis-checkpoint

.PHONY: run
run: ## Run the API with autoreload
	$(UV) run agentmesh serve --reload

.PHONY: worker
worker: ## Run a queue worker
	$(UV) run agentmesh worker

.PHONY: demo
demo: ## Submit a sample task and stream events from the CLI
	$(UV) run agentmesh run "Research LangGraph supervisor patterns and write a short brief" --watch

.PHONY: test
test: ## Run the test suite
	$(UV) run pytest

.PHONY: cov
cov: ## Run tests with coverage
	$(UV) run pytest --cov=agentmesh --cov-report=term-missing --cov-report=xml

.PHONY: lint
lint: ## Lint
	$(UV) run ruff check src tests

.PHONY: fmt
fmt: ## Format
	$(UV) run ruff format src tests
	$(UV) run ruff check --fix src tests

.PHONY: typecheck
typecheck: ## Static type check
	$(UV) run mypy

.PHONY: check
check: lint typecheck test ## Lint + typecheck + test

.PHONY: docker-build
docker-build: ## Build the Docker image
	$(COMPOSE) build

.PHONY: up
up: ## Start redis + api + workers
	$(COMPOSE) up -d --build
	@echo "API docs: http://localhost:8000/docs"

.PHONY: down
down: ## Stop the stack
	$(COMPOSE) down

.PHONY: clean
clean: ## Remove caches and build artifacts
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
