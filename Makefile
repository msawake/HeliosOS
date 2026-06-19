# Helios OS local dev — Postgres + FastAPI backend + Next.js dashboard.
#
#   make start      # stop anything stale, then bring up pg → backend → dashboard
#   make stop       # kill all three (ports 3000, 5055, container forgeos-pg-local)
#   make logs       # tail backend + dashboard logs
#   make psql       # interactive psql inside the running Postgres container
#   make reset      # nuke the database volume and restart fresh
#
# Requires: Docker Desktop running, python3.11, node, npm.

BACKEND_PORT      ?= 5000
DASH_PORT         ?= 3000
MC_PLATFORM_PORT  ?= 5099
MC_VENV           ?= .venv-platform
MC_PY             ?= python3.11
MC_COMPANY        ?= leadforge
# Reuse the Postgres started by docker-compose (forgeos-postgres-1 on :5433)
# rather than spinning up a second, separate container. Credentials match
# docker-compose.yaml's postgres service.
PG_PORT           ?= 5433
PG_CONTAINER      ?= forgeos-postgres-1
PG_USER           ?= leadforge_admin
PG_PASSWORD       ?= forgeoslocal
PG_DB             ?= leadforge
PG_IMAGE          ?= pgvector/pgvector:pg16
COMPANY           ?= leadforge
BACKEND_LOG       ?= /tmp/forgeos-backend.log
DASH_LOG          ?= /tmp/forgeos-dashboard.log
# The Next.js dashboard now lives in its own repo (github.com/antonibergas-hue/
# forgeos-dashboard). `make dash` expects it checked out as a sibling of helios.
DASH_DIR          ?= $(CURDIR)/../forgeos-dashboard

DATABASE_URL := postgresql://$(PG_USER):$(PG_PASSWORD)@localhost:$(PG_PORT)/$(PG_DB)

.PHONY: help
help:
	@echo "Targets:"
	@echo "  make start    Stop stale processes, start pg → backend → dashboard"
	@echo "  make stop     Kill processes on $(BACKEND_PORT)/$(DASH_PORT) and stop pg container"
	@echo "  make pg       Start Postgres container only"
	@echo "  make backend  Start backend only (needs pg up)"
	@echo "  make dash     Start dashboard only"
	@echo "  make logs     Tail backend + dashboard logs"
	@echo "  make psql     Interactive psql shell"
	@echo "  make reset    Stop, delete pg volume, restart fresh"
	@echo "  make status   Show what's running"
	@echo ""
	@echo "Lightweight platform workflow (no Postgres, in-memory):"
	@echo "  make mc-platform   Boot platform on $(MC_PLATFORM_PORT) — drive it with 'forgeos mc fleet'"
	@echo "  make mc-setup      Create $(MC_VENV) and install platform deps (run once)"
	@echo "  make migrate       Apply pending SQL migrations to local Postgres"
	@echo "  make free-port PORT=N   Kill whatever is listening on port N"

# ---------------------------------------------------------------------------
# Stop targets — always safe to run, never errors on "nothing to kill"
# ---------------------------------------------------------------------------
.PHONY: stop stop-backend stop-dash stop-pg stop-mc-platform free-port

stop: stop-backend stop-dash stop-pg stop-mc-platform
	@echo "✓ all stopped"

# Free an arbitrary TCP port. Usage: make free-port PORT=5099
free-port:
	@if [ -z "$(PORT)" ]; then echo "✗ specify PORT=<n>"; exit 1; fi
	@echo "→ freeing port $(PORT)"
	@-lsof -tiTCP:$(PORT) -sTCP:LISTEN 2>/dev/null | xargs -r kill -9 2>/dev/null || true

stop-mc-platform:
	@echo "→ stopping mc-platform ($(MC_PLATFORM_PORT))"
	@-pkill -f "src.bootstrap" 2>/dev/null || true
	@-lsof -tiTCP:$(MC_PLATFORM_PORT) -sTCP:LISTEN 2>/dev/null | xargs -r kill -9 2>/dev/null || true

stop-backend:
	@echo "→ stopping backend ($(BACKEND_PORT))"
	@-pkill -f "src.bootstrap" 2>/dev/null || true
	@-lsof -tiTCP:$(BACKEND_PORT) -sTCP:LISTEN 2>/dev/null | xargs -r kill -9 2>/dev/null || true

stop-dash:
	@echo "→ stopping dashboard ($(DASH_PORT))"
	@-pkill -f "next dev" 2>/dev/null || true
	@-lsof -tiTCP:$(DASH_PORT) -sTCP:LISTEN 2>/dev/null | xargs -r kill -9 2>/dev/null || true

stop-pg:
	@echo "→ leaving postgres ($(PG_CONTAINER)) running — it's managed by docker-compose"

# ---------------------------------------------------------------------------
# Start targets
# ---------------------------------------------------------------------------
.PHONY: pg backend dash start

pg:
	@command -v docker >/dev/null || { echo "✗ docker not found"; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "✗ Docker daemon not running. Open Docker Desktop and retry."; exit 1; }
	@echo "→ using docker-compose postgres ($(PG_CONTAINER)) on $(PG_PORT)"
	@if ! docker ps --filter "name=$(PG_CONTAINER)" --filter "status=running" --format '{{.Names}}' | grep -q .; then \
		echo "→ $(PG_CONTAINER) not running — starting via docker compose"; \
		docker compose up -d postgres redis >/dev/null 2>&1 || { echo "✗ failed to start compose postgres"; exit 1; }; \
	fi
	@echo "→ waiting for postgres to accept connections"
	@for i in $$(seq 1 30); do \
		docker exec $(PG_CONTAINER) pg_isready -U $(PG_USER) -d $(PG_DB) >/dev/null 2>&1 && { echo "✓ postgres ready"; exit 0; }; \
		sleep 1; \
	done; \
	echo "✗ postgres did not become ready"; exit 1

backend: stop-backend stop-mc-platform
	@echo "→ starting backend on $(BACKEND_PORT) (DATABASE_URL=$(DATABASE_URL))"
	@cd "$(CURDIR)" && PYTHONPATH=.:a2h DATABASE_URL="$(DATABASE_URL)" \
		nohup python3.11 -m src.bootstrap --no-auth --port $(BACKEND_PORT) --company $(COMPANY) --dashboard \
		> $(BACKEND_LOG) 2>&1 &
	@for i in $$(seq 1 30); do \
		curl -sf --max-time 2 http://localhost:$(BACKEND_PORT)/api/health >/dev/null 2>&1 && { echo "✓ backend ready"; exit 0; }; \
		sleep 2; \
	done; \
	echo "✗ backend did not become ready — see $(BACKEND_LOG)"; tail -20 $(BACKEND_LOG); exit 1

dash: stop-dash
	@if [ ! -d "$(DASH_DIR)" ]; then \
		echo "⚠ dashboard repo not found at $(DASH_DIR) — skipping."; \
		echo "  Clone it as a sibling: git clone git@github.com:antonibergas-hue/forgeos-dashboard.git $(DASH_DIR)"; \
		exit 0; \
	fi
	@echo "→ starting dashboard on $(DASH_PORT) (proxying to http://localhost:$(BACKEND_PORT))"
	@cd "$(DASH_DIR)" && FORGEOS_API_URL=http://localhost:$(BACKEND_PORT) \
		nohup npm run dev > $(DASH_LOG) 2>&1 &
	@for i in $$(seq 1 30); do \
		curl -sf --max-time 2 http://localhost:$(DASH_PORT) >/dev/null 2>&1 && { echo "✓ dashboard ready"; exit 0; }; \
		sleep 2; \
	done; \
	echo "✗ dashboard did not become ready — see $(DASH_LOG)"; tail -20 $(DASH_LOG); exit 1

start: stop pg backend dash
	@echo ""
	@echo "════════════════════════════════════════════════════════════"
	@echo "  Dashboard:  http://localhost:$(DASH_PORT)"
	@echo "  Backend:    http://localhost:$(BACKEND_PORT)"
	@echo "  Postgres:   localhost:$(PG_PORT) (user=$(PG_USER) db=$(PG_DB))"
	@echo "  Logs:       make logs"
	@echo "════════════════════════════════════════════════════════════"

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
.PHONY: logs psql reset status

logs:
	@tail -n 80 -f $(BACKEND_LOG) $(DASH_LOG)

psql:
	@docker exec -it $(PG_CONTAINER) psql -U $(PG_USER) -d $(PG_DB)

reset: stop
	@docker volume rm forgeos_pg_data >/dev/null 2>&1 || true
	@$(MAKE) start

status:
	@echo "── Postgres ──"; docker ps --filter "name=$(PG_CONTAINER)" --format '{{.Names}}  {{.Status}}' | sed 's/^/  /' || echo "  not running"
	@echo "── Backend  ──"; lsof -nP -iTCP:$(BACKEND_PORT) -sTCP:LISTEN 2>/dev/null | tail -n +2 | awk '{print "  "$$1" PID="$$2}' || echo "  not running"
	@echo "── Dashboard ─"; lsof -nP -iTCP:$(DASH_PORT) -sTCP:LISTEN 2>/dev/null | tail -n +2 | awk '{print "  "$$1" PID="$$2}' || echo "  not running"

# ---------------------------------------------------------------------------
# Lightweight platform workflow — boots a lightweight platform (in-memory,
# no Postgres) on $(MC_PLATFORM_PORT). Drive it from a second terminal with
# the `forgeos mc` CLI (e.g. `forgeos mc fleet`).
# ---------------------------------------------------------------------------
.PHONY: mc-setup mc-platform migrate

# Apply pending SQL migrations against the local Postgres. Idempotent —
# already-applied versions are tracked in the schema_migrations table.
migrate:
	@[ -x $(MC_VENV)/bin/python ] || { echo "✗ $(MC_VENV) missing — run 'make mc-setup' first"; exit 1; }
	@docker exec $(PG_CONTAINER) pg_isready -U $(PG_USER) -d $(PG_DB) >/dev/null 2>&1 \
		|| { echo "✗ Postgres not running — 'make pg' first"; exit 1; }
	@echo "→ applying migrations"
	@DATABASE_URL="$(DATABASE_URL)" PYTHONPATH=. $(MC_VENV)/bin/python -m src.core.migrations
	@echo "→ schema_migrations:"
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -d $(PG_DB) -tAc \
		"SELECT version FROM schema_migrations ORDER BY version;" | sed 's/^/    /'

mc-setup:
	@command -v $(MC_PY) >/dev/null || { echo "✗ $(MC_PY) not found — install Python 3.11+"; exit 1; }
	@[ -d $(MC_VENV) ] || { echo "→ creating $(MC_VENV)"; $(MC_PY) -m venv $(MC_VENV); $(MC_VENV)/bin/pip install -q --upgrade pip; }
	@echo "→ installing platform deps into $(MC_VENV)"
	@$(MC_VENV)/bin/pip install -q -e ".[dev]"
	@echo "→ installing optional deps (psycopg, jsonschema)"
	@$(MC_VENV)/bin/pip install -q 'psycopg[binary]' psycopg_pool jsonschema mcp
	@echo "✓ ready. Run: make pg (once) then: make mc-platform"

mc-platform: stop-mc-platform
	@[ -x $(MC_VENV)/bin/python ] || { echo "✗ $(MC_VENV) missing — run 'make mc-setup' first"; exit 1; }
	@if docker exec $(PG_CONTAINER) pg_isready -U $(PG_USER) -d $(PG_DB) >/dev/null 2>&1; then \
		echo "→ Postgres detected on :$(PG_PORT) — wiring DATABASE_URL for persistence"; \
		DB_URL="$(DATABASE_URL)"; \
	else \
		echo "→ no Postgres on :$(PG_PORT) — booting IN-MEMORY (run 'make pg' first for persistence)"; \
		DB_URL=""; \
	fi; \
	echo "→ booting platform on $(MC_PLATFORM_PORT) (company=$(MC_COMPANY))"; \
	echo "  drive it with: forgeos mc fleet"; \
	DATABASE_URL="$$DB_URL" PYTHONPATH=.:a2h $(MC_VENV)/bin/python -m src.bootstrap --no-auth --dashboard --port $(MC_PLATFORM_PORT) --company $(MC_COMPANY)
