# ForgeOS local dev — Rust CLI + Python server.
#
# The `forgeos` CLI is a Rust binary (forgeos-cli/) that talks to the Python
# platform via an HTTP server (`forgeos-server`). Typical loop:
#
#   make setup            # one-time: venv + deps + cargo build
#   make server           # start the Python platform on :5055 (background)
#   make forgeos ARGS="health"
#   make forgeos ARGS="deploy examples/jira-greeter-v2/manifest.yaml"
#   make forgeos ARGS="config set-credential ANTHROPIC_API_KEY sk-..."
#   make stop-server
#
# Postgres is optional. Without it the server runs in-memory. Bring up
# Postgres when you want persistence under the platform's process table:
#
#   make pg && make migrate
#
# Requires: python3.11+, Rust toolchain (cargo, rustc), Docker (for pg).

VENV              ?= .venv
PY                ?= python3.13
VENV_PY           := $(VENV)/bin/python
VENV_PIP          := $(VENV)/bin/pip

CARGO             ?= cargo
RUST_DIR          := forgeos-cli
RUST_BIN_DEBUG    := $(RUST_DIR)/target/debug/forgeos
RUST_BIN_RELEASE  := $(RUST_DIR)/target/release/forgeos

SERVER_HOST       ?= 127.0.0.1
SERVER_PORT       ?= 5055
SERVER_LOG        ?= /tmp/forgeos-server.log

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
	@echo "  make setup                            One-time venv + pip + cargo build"
	@echo "  make server                           Start forgeos-server on $(SERVER_HOST):$(SERVER_PORT)"
	@echo "  make stop-server                      Stop forgeos-server"
	@echo "  make forgeos ARGS=\"<args>\"            Run the Rust CLI (debug build)"
	@echo "  make forgeos-release                  Build optimized Rust binary"
	@echo "  make pg                               Start local Postgres (optional)"
	@echo "  make migrate                          Apply SQL migrations"
	@echo "  make stop                             Stop server + postgres"
	@echo "  make status                           Show what's running"
	@echo "  make free-port PORT=N                 Kill whatever's on port N"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
.PHONY: setup setup-py setup-rust

setup: setup-py setup-rust
	@echo "✓ ready. Try: make server  &&  make forgeos ARGS=\"health\""

setup-py:
	@command -v $(PY) >/dev/null || { echo "✗ $(PY) not found — install Python 3.11+"; exit 1; }
	@[ -d $(VENV) ] || { echo "→ creating $(VENV)"; $(PY) -m venv $(VENV); $(VENV_PIP) install -q --upgrade pip; }
	@echo "→ installing project deps (editable, with server + mcp extras)"
	@$(VENV_PIP) install -q -e ".[dev,server,mcp]"

setup-rust:
	@command -v $(CARGO) >/dev/null || { echo "✗ cargo not found — install Rust via https://rustup.rs"; exit 1; }
	@echo "→ cargo build (debug)"
	@cd $(RUST_DIR) && $(CARGO) build --quiet

# ---------------------------------------------------------------------------
# Rust CLI
# ---------------------------------------------------------------------------
.PHONY: forgeos cli forgeos-release

forgeos:
	@[ -x $(RUST_BIN_DEBUG) ] || { echo "→ building Rust CLI"; cd $(RUST_DIR) && $(CARGO) build --quiet; }
	@$(RUST_BIN_DEBUG) $(ARGS)

cli: forgeos

forgeos-release:
	@cd $(RUST_DIR) && $(CARGO) build --release
	@echo "✓ optimized binary at $(RUST_BIN_RELEASE)"

# ---------------------------------------------------------------------------
# Python HTTP server (forgeos-server)
# ---------------------------------------------------------------------------
.PHONY: server stop-server

server: stop-server
	@[ -x $(VENV_PY) ] || { echo "✗ $(VENV) missing — run 'make setup' first"; exit 1; }
	@echo "→ starting forgeos-server on $(SERVER_HOST):$(SERVER_PORT)"
	@PYTHONPATH=.:a2h nohup $(VENV_PY) -m src.forgeos_sdk.local_server \
		--host $(SERVER_HOST) --port $(SERVER_PORT) \
		> $(SERVER_LOG) 2>&1 &
	@for i in $$(seq 1 30); do \
		sleep 1; \
		curl -sf http://$(SERVER_HOST):$(SERVER_PORT)/api/health >/dev/null 2>&1 \
			&& { echo "✓ server ready (token in ~/.forgeos/server.lock)"; exit 0; }; \
	done; \
	echo "✗ server did not become ready — see $(SERVER_LOG)"; tail -20 $(SERVER_LOG); exit 1

stop-server:
	@echo "→ stopping forgeos-server"
	@-pkill -f "src.forgeos_sdk.local_server" 2>/dev/null || true
	@-lsof -tiTCP:$(SERVER_PORT) -sTCP:LISTEN 2>/dev/null | xargs -r kill -9 2>/dev/null || true
	@-rm -f $$HOME/.forgeos/server.lock 2>/dev/null || true

# ---------------------------------------------------------------------------
# Postgres (optional)
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
	@-docker rm -f $(PG_CONTAINER) >/dev/null 2>&1 || true

migrate:
	@[ -x $(VENV_PY) ] || { echo "✗ $(VENV) missing — run 'make setup' first"; exit 1; }
	@docker exec $(PG_CONTAINER) pg_isready -U $(PG_USER) -d $(PG_DB) >/dev/null 2>&1 \
		|| { echo "✗ Postgres not running — 'make pg' first"; exit 1; }
	@DATABASE_URL="$(DATABASE_URL)" PYTHONPATH=.:a2h $(VENV_PY) -m src.core.migrations
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -d $(PG_DB) -tAc \
		"SELECT version FROM schema_migrations ORDER BY version;" | sed 's/^/    /'

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
.PHONY: stop psql status free-port

stop: stop-server stop-pg
	@echo "✓ stopped"

free-port:
	@if [ -z "$(PORT)" ]; then echo "✗ specify PORT=<n>"; exit 1; fi
	@-lsof -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null | xargs -r kill -9 2>/dev/null || true

psql:
	@docker exec -it $(PG_CONTAINER) psql -U $(PG_USER) -d $(PG_DB)

status:
	@echo "── Venv      ──"; [ -x $(VENV_PY) ] && echo "  $(VENV_PY) present" || echo "  not set up"
	@echo "── Rust bin  ──"; [ -x $(RUST_BIN_DEBUG) ] && echo "  $(RUST_BIN_DEBUG) present" || echo "  not built (run 'make setup')"
	@echo "── Server    ──"; lsof -nP -iTCP:$(SERVER_PORT) -sTCP:LISTEN 2>/dev/null | tail -n +2 | awk '{print "  "$$1" PID="$$2}' | grep . || echo "  not running"
	@echo "── Postgres  ──"; docker ps --filter "name=$(PG_CONTAINER)" --format '  {{.Names}}  {{.Status}}' 2>/dev/null | grep . || echo "  not running"
