# ForgeOS local dev — thin-client model.
#
# Since the Mission Control FastAPI backend was removed, there is no
# "backend" or "dashboard" process to run. The `forgeos` CLI talks to the
# platform in-process (see src/forgeos_sdk/local_runtime.py), so everyday
# work just means invoking the CLI inside the project venv.
#
#   make forgeos ARGS="health"
#   make forgeos ARGS="deploy examples/jira-greeter-v2/manifest.yaml"
#   make forgeos ARGS="config set-credential ANTHROPIC_API_KEY sk-..."
#
# Postgres is optional. The CLI runs without it; bring it up when you want
# persistence under the platform's process table / agent runs store:
#
#   make pg && make migrate
#
# Requires: python3.11+, Docker Desktop (only for `make pg` / `make migrate`).

VENV              ?= .venv
PY                ?= python3.13
VENV_PY           := $(VENV)/bin/python
VENV_PIP          := $(VENV)/bin/pip

PG_PORT           ?= 5432
PG_CONTAINER      ?= forgeos-pg-local
PG_USER           ?= forgeos
PG_PASSWORD       ?= forgeos
PG_DB             ?= forgeos
PG_IMAGE          ?= pgvector/pgvector:pg16

DATABASE_URL := postgresql://$(PG_USER):$(PG_PASSWORD)@localhost:$(PG_PORT)/$(PG_DB)

.PHONY: help
help:
	@echo "Targets:"
	@echo "  make forgeos ARGS=\"<subcommand ...>\"  Run the forgeos CLI in-process"
	@echo "  make cli ARGS=\"<subcommand ...>\"      Alias for 'make forgeos'"
	@echo "  make setup                            Create $(VENV) and install deps"
	@echo "  make pg                               Start local Postgres container (optional)"
	@echo "  make migrate                          Apply SQL migrations against local Postgres"
	@echo "  make psql                             Interactive psql shell"
	@echo "  make stop                             Stop the Postgres container"
	@echo "  make reset                            Stop + drop pg volume + restart"
	@echo "  make status                           Show what's running"
	@echo "  make free-port PORT=N                 Kill whatever is listening on port N"

# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------
.PHONY: forgeos cli

forgeos:
	@[ -x $(VENV_PY) ] || { echo "✗ $(VENV) missing — run 'make setup' first"; exit 1; }
	@PYTHONPATH=. $(VENV_PY) -m src.forgeos_sdk.cli $(ARGS)

cli: forgeos

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
.PHONY: setup

setup:
	@command -v $(PY) >/dev/null || { echo "✗ $(PY) not found — install Python 3.11+"; exit 1; }
	@[ -d $(VENV) ] || { echo "→ creating $(VENV)"; $(PY) -m venv $(VENV); $(VENV_PIP) install -q --upgrade pip; }
	@echo "→ installing project deps (editable)"
	@$(VENV_PIP) install -q -e ".[dev]"
	@echo "✓ ready. Try: make forgeos ARGS=\"health\""

# ---------------------------------------------------------------------------
# Postgres (optional — agents work fine without it, in-memory)
# ---------------------------------------------------------------------------
.PHONY: pg stop-pg migrate

pg: stop-pg
	@command -v docker >/dev/null || { echo "✗ docker not found"; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "✗ Docker daemon not running. Open Docker Desktop and retry."; exit 1; }
	@echo "→ starting postgres on $(PG_PORT)"
	@docker run -d --name $(PG_CONTAINER) \
		-e POSTGRES_USER=$(PG_USER) \
		-e POSTGRES_PASSWORD=$(PG_PASSWORD) \
		-e POSTGRES_DB=$(PG_DB) \
		-p $(PG_PORT):5432 \
		-v forgeos_pg_data:/var/lib/postgresql/data \
		$(PG_IMAGE) >/dev/null
	@echo "→ waiting for postgres to accept connections"
	@for i in $$(seq 1 30); do \
		docker exec $(PG_CONTAINER) pg_isready -U $(PG_USER) -d $(PG_DB) >/dev/null 2>&1 && { echo "✓ postgres ready"; exit 0; }; \
		sleep 1; \
	done; \
	echo "✗ postgres did not become ready"; exit 1

stop-pg:
	@echo "→ stopping postgres ($(PG_CONTAINER))"
	@-docker rm -f $(PG_CONTAINER) >/dev/null 2>&1 || true

migrate:
	@[ -x $(VENV_PY) ] || { echo "✗ $(VENV) missing — run 'make setup' first"; exit 1; }
	@docker exec $(PG_CONTAINER) pg_isready -U $(PG_USER) -d $(PG_DB) >/dev/null 2>&1 \
		|| { echo "✗ Postgres not running — 'make pg' first"; exit 1; }
	@echo "→ applying migrations"
	@DATABASE_URL="$(DATABASE_URL)" PYTHONPATH=. $(VENV_PY) -m src.core.migrations
	@echo "→ schema_migrations:"
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -d $(PG_DB) -tAc \
		"SELECT version FROM schema_migrations ORDER BY version;" | sed 's/^/    /'

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
.PHONY: stop psql reset status free-port

stop: stop-pg
	@echo "✓ stopped"

# Free an arbitrary TCP port. Usage: make free-port PORT=5432
free-port:
	@if [ -z "$(PORT)" ]; then echo "✗ specify PORT=<n>"; exit 1; fi
	@echo "→ freeing port $(PORT)"
	@-lsof -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null | xargs -r kill -9 2>/dev/null || true

psql:
	@docker exec -it $(PG_CONTAINER) psql -U $(PG_USER) -d $(PG_DB)

reset: stop
	@docker volume rm forgeos_pg_data >/dev/null 2>&1 || true
	@$(MAKE) pg

status:
	@echo "── Venv     ──"; [ -x $(VENV_PY) ] && echo "  $(VENV_PY) present" || echo "  not set up (run 'make setup')"
	@echo "── Postgres ──"; docker ps --filter "name=$(PG_CONTAINER)" --format '  {{.Names}}  {{.Status}}' 2>/dev/null | grep . || echo "  not running"
