# ForgeOS local dev — Postgres + FastAPI backend + Next.js dashboard.
#
#   make start      # stop anything stale, then bring up pg → backend → dashboard
#   make stop       # kill all three (ports 3000, 5055, container forgeos-pg-local)
#   make logs       # tail backend + dashboard logs
#   make psql       # interactive psql inside the running Postgres container
#   make reset      # nuke the database volume and restart fresh
#
# Requires: Docker Desktop running, python3.11, node, npm.

BACKEND_PORT      ?= 5055
DASH_PORT         ?= 3000
PG_PORT           ?= 5432
PG_CONTAINER      ?= forgeos-pg-local
PG_USER           ?= forgeos
PG_PASSWORD       ?= forgeos
PG_DB             ?= forgeos
PG_IMAGE          ?= pgvector/pgvector:pg16
COMPANY           ?= leadforge
BACKEND_LOG       ?= /tmp/forgeos-backend.log
DASH_LOG          ?= /tmp/forgeos-dashboard.log

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

# ---------------------------------------------------------------------------
# Stop targets — always safe to run, never errors on "nothing to kill"
# ---------------------------------------------------------------------------
.PHONY: stop stop-backend stop-dash stop-pg

stop: stop-backend stop-dash stop-pg
	@echo "✓ all stopped"

stop-backend:
	@echo "→ stopping backend ($(BACKEND_PORT))"
	@-pkill -f "src.bootstrap" 2>/dev/null || true
	@-lsof -tiTCP:$(BACKEND_PORT) -sTCP:LISTEN 2>/dev/null | xargs -r kill -9 2>/dev/null || true

stop-dash:
	@echo "→ stopping dashboard ($(DASH_PORT))"
	@-pkill -f "next dev" 2>/dev/null || true
	@-lsof -tiTCP:$(DASH_PORT) -sTCP:LISTEN 2>/dev/null | xargs -r kill -9 2>/dev/null || true

stop-pg:
	@echo "→ stopping postgres ($(PG_CONTAINER))"
	@-docker rm -f $(PG_CONTAINER) >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Start targets
# ---------------------------------------------------------------------------
.PHONY: pg backend dash start

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

backend: stop-backend
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
	@echo "→ starting dashboard on $(DASH_PORT) (proxying to http://localhost:$(BACKEND_PORT))"
	@cd "$(CURDIR)/dashboard" && FORGEOS_API_URL=http://localhost:$(BACKEND_PORT) \
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
