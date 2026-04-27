.PHONY: help install install-dev install-prod install-all \
       run forgeos dashboard \
       test test-file test-match \
       lint typecheck check \
       migrate \
       clean

PYTHON   ?= /opt/homebrew/opt/python@3.11/bin/python3.11
PIP      ?= $(PYTHON) -m pip
PORT     ?= 5000

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ──────────────────────────────────────────────
# Installation
# ──────────────────────────────────────────────

install: ## Install base dependencies
	$(PIP) install -e .

install-dev: ## Install with dev extras (pytest, ruff, mypy)
	$(PIP) install -e ".[dev]"

install-prod: ## Install with production extras
	$(PIP) install -e ".[production]"

install-all: install-dev ## Install Python deps + dashboard Node modules
	cd dashboard && npm install

# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────

run: ## Boot the platform (port=$(PORT), no-auth mode)
	PYTHONPATH=. $(PYTHON) -m src.bootstrap --no-auth --dashboard --port $(PORT)

forgeos: ## ForgeOS CLI — define agents, manage Docker, deploy, chat, monitor
	PYTHONPATH=. $(PYTHON) backend/forgeos

dashboard: ## Start the Next.js dashboard (dev server)
	cd dashboard && npm run dev

# ──────────────────────────────────────────────
# Testing
# ──────────────────────────────────────────────

test: ## Run all tests
	PYTHONPATH=. $(PYTHON) -m pytest

test-file: ## Run a single test file (FILE=tests/test_xxx.py)
	PYTHONPATH=. $(PYTHON) -m pytest $(FILE)

test-match: ## Run tests matching a pattern (K=pattern)
	PYTHONPATH=. $(PYTHON) -m pytest -k "$(K)"

# ──────────────────────────────────────────────
# Code quality
# ──────────────────────────────────────────────

lint: ## Run ruff linter on src/ and tests/
	ruff check src/ tests/

typecheck: ## Run mypy type checker on src/
	mypy src/

check: lint typecheck ## Run lint + typecheck

# ──────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────

migrate: ## Run database migrations
	PYTHONPATH=. $(PYTHON) -m src.core.migrations

# ──────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────

clean: ## Remove Python caches and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/
