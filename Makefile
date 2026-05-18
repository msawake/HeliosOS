.PHONY: help install install-dev install-prod install-all \
       run forgeos dashboard \
       test test-file test-match \
       e2e e2e-headed e2e-report \
       lint typecheck check \
       migrate \
       clean

PORT     ?= 5000

# Use uv if available, otherwise fall back to plain venv + pip.
# uv: creates .venv automatically and manages it.
# venv: explicitly created at .venv/ and activated per-command via PYTHON/PIP.
HAS_UV   := $(shell command -v uv 2>/dev/null)

VENV       = .venv
PYTHON     = $(VENV)/bin/python

ifdef HAS_UV
  INSTALL    = uv pip install
else
  PIP        = $(PYTHON) -m pip
  INSTALL    = $(PIP) install
endif

RUN_PYTHON = $(PYTHON)
RUN_PYTEST = $(PYTHON) -m pytest

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ──────────────────────────────────────────────
# Installation
# ──────────────────────────────────────────────

ifdef HAS_UV
.venv:
	uv venv
else
.venv:
	python3 -m venv .venv
endif

install: .venv ## Install base dependencies
	$(INSTALL) -e .

install-dev: .venv ## Install with dev extras (pytest, ruff, mypy)
	$(INSTALL) -e ".[dev]"

install-prod: .venv ## Install with production extras
	$(INSTALL) -e ".[production]"

install-all: install-dev ## Install Python deps + dashboard Node modules
	cd dashboard && npm install

# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────

run: ## Boot the platform (port=$(PORT), no-auth mode)
	PYTHONPATH=. $(RUN_PYTHON) -m src.bootstrap --no-auth --dashboard --port $(PORT)

forgeos: ## ForgeOS CLI — define agents, manage Docker, deploy, chat, monitor
	PYTHONPATH=. $(RUN_PYTHON) backend/forgeos

dashboard: ## Start the Next.js dashboard (dev server)
	cd dashboard && npm run dev

# ──────────────────────────────────────────────
# Testing
# ──────────────────────────────────────────────

test: ## Run all tests
	PYTHONPATH=. $(RUN_PYTEST)

test-file: ## Run a single test file (FILE=tests/test_xxx.py)
	PYTHONPATH=. $(RUN_PYTEST) $(FILE)

test-match: ## Run tests matching a pattern (K=pattern)
	PYTHONPATH=. $(RUN_PYTEST) -k "$(K)"

e2e: ## Run Playwright e2e suite (headless)
	cd e2e && npx playwright test

e2e-headed: ## Run Playwright e2e suite (headed browser)
	cd e2e && npx playwright test --headed

e2e-report: ## Open the last Playwright HTML report
	cd e2e && npx playwright show-report

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
	PYTHONPATH=. $(RUN_PYTHON) -m src.core.migrations

# ──────────────────────────────────────────────
# Cleanup
# ──────────────────────────────────────────────

clean: ## Remove venv, Python caches, and build artifacts
	rm -rf .venv
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/
