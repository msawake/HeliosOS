# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What This Is

**ForgeOS v3.0** — A multi-stack AI agent platform that deploys, orchestrates, and manages AI agents across four framework adapters (ForgeOS, CrewAI, Google ADK, OpenClaw) with five execution lifecycles and a Next.js dashboard.

**Key distinction:** ForgeOS is the *framework* (the operating system). Agents are the *programs* that run inside it. The framework provides scheduling, tool execution, LLM routing, persistence, and monitoring. Agents define what work gets done.

Five company packages are built: **LeadForge AI** (B2B sales), **DealForge AI** (M&A), **TravelForge AI** (travel), **InsureForge AI** (insurance), **HomeForge AI** (real estate).

## Commands

```bash
# Install (Python 3.11+)
pip install -e ".[dev]"

# Run all tests (730 tests)
PYTHONPATH=. python -m pytest

# Run a single test
PYTHONPATH=. python -m pytest tests/test_platform_executor.py

# Lint / type check
ruff check src/ tests/
mypy src/

# Boot the platform
PYTHONPATH=. python -m src.bootstrap --no-auth --dashboard --port 5000

# Next.js dashboard (separate terminal)
cd dashboard && npm install && npm run dev
```

Note: `PYTHONPATH=.` is required because `stacks/` is a top-level package alongside `src/`.

## Three-Layer Architecture

### 1. Stack Adapters (`stacks/`)

`AgentStackAdapter` ABC in `stacks/base.py`. Four implementations:

| Adapter | File | Runtime | Fallback |
|---------|------|---------|----------|
| ForgeOS | `stacks/forgeos/adapter.py` | Native agentic loop | — |
| CrewAI | `stacks/crewai/adapter.py` | CrewAI SDK (Crew.kickoff) | Platform loop |
| ADK | `stacks/adk/adapter.py` | Google ADK Runner | Platform loop |
| OpenClaw | `stacks/openclaw/adapter.py` | HTTP gateway subprocess | Platform loop |

Each provides: `create_agent()`, `invoke()`, `start_loop()`, `stop()`, `scaffold_files()`.

### 2. Platform Layer (`src/platform/`)

Stack-agnostic orchestration shared by all agents:

- `registry.py` — Universal agent registry (query by stack/type/owner/department)
- `executor.py` — Central dispatcher: deploy, invoke, wire execution lifecycle, recover
- `scheduler.py` — Cron-based scheduling for scheduled agents
- `event_bus.py` — Pub/sub for event-driven agents
- `llm_router.py` — Routes to Anthropic/OpenAI, retry with backoff, failover, streaming
- `agentic_loop.py` — LLM -> tool_use -> execute -> tool_result -> LLM loop (sync + streaming)
- `audit.py` — Records all platform events
- `alerts.py` — Multi-destination alerts (Slack, PagerDuty, log)
- `metrics.py` — Prometheus metrics (14 families)

### 3. Core + Companies (`src/core/`, `src/companies/`, `src/mcp/`)

- `agent_invoker.py` — Legacy agent orchestration (3-tier hierarchy)
- `claude_client.py` — Provider-agnostic agentic loop (pre-platform layer)
- `model_client.py` — `LLMClient` protocol + Anthropic/OpenAI implementations
- `hooks.py` — 7-check governance chain (budget, rate limit, auth, cost, compliance, Slack, audit)
- `database.py` — Multi-tenant PostgreSQL with RLS, connection pooling
- `session_store.py` — In-memory or PostgreSQL session persistence
- `mcp/tool_executor.py` — Routes `mcp__*` to MCP servers, `company__*` to in-process handlers
- `mcp/server_manager.py` — MCP server lifecycle (connect, discover tools, disconnect)
- `mcp/client_mcp_manager.py` — Per-client MCP connections with LRU eviction
- `companies/<id>/` — Each provides `agent_configs.py`, `workflows.py`, `knowledge.py`, `config.yaml`, `demo.py`

### Dashboard

Next.js 15 + React 19 + Tailwind CSS in `dashboard/`. Talks to FastAPI backend (61 endpoints). OpenAI-inspired dark theme.

### Infrastructure

- `infrastructure/docker/` — Dockerfile + docker-compose (Postgres + Redis + API)
- `infrastructure/database/` — 5 SQL migrations (001-005)
- `infrastructure/terraform/gcp/` — Cloud SQL, Redis, Cloud Run, VPC, Secret Manager
- `deploy/k8s/` — Kubernetes manifests with Kustomize overlays (dev/staging/prod)
- `.github/workflows/` — CI: test -> build -> push to GHCR

## Agent Model

- **5 execution types:** always_on, scheduled, event_driven, reflex, autonomous
- **3 ownership types:** personal, shared, client
- **3-tier hierarchy** (ForgeOS stack): Executives (Opus) -> Department Leads (Opus) -> Workers (Sonnet/Haiku)
- **Multi-model:** `claude-*` -> Anthropic, `gpt-*`/`o3-*` -> OpenAI (auto-detected from model prefix)
- **74 deployed agents:** 53 shared + 21 personal across sales, marketing, finance, HR, legal, operations

## Key Conventions

- **Multi-tenancy:** All DB tables have `tenant_id` + RLS. `DatabaseClient.tenant(id)` sets session context via `set_config('app.current_tenant', ...)`.
- **Graceful degradation:** No API key -> simulation. No DB -> in-memory. No Redis -> in-memory. No MCP -> "not connected". No SDK -> platform fallback.
- **`asyncio_mode = "auto"`** in pytest.
- **`.env` for secrets** — never committed.
- **`agents/` directory** is gitignored — personal/shared agent configs live there at runtime.

## Domain Context (LeadForge)

- Lead scoring: BANT framework, SQL threshold >= 70 with >= 2 signals
- Maximum 50 outreach emails per SDR per day per client
- CAN-SPAM and GDPR compliance required for all outreach
- Financial thresholds: <$1K dept lead, $1K-$5K CFO, $5K-$10K CEO, >$10K human board
- Strict per-client data isolation
