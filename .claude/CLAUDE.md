# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

ForgeOS — a multi-tenant SaaS platform for running AI-operated companies as multi-agent swarms. Each "company" is a pluggable package under `src/companies/` with its own agent definitions, workflows, knowledge base, and demo. Five companies are built: **LeadForge AI** (B2B sales), **DealForge AI** (M&A deals), **TravelForge AI** (travel booking), **InsureForge AI** (insurance), **HomeForge AI** (real estate).

## Commands

```bash
# Install (Python 3.11+)
pip install -e ".[dev]"

# Run all tests (325 tests)
python -m pytest

# Run a single test file
python -m pytest tests/test_workflows.py

# Lint
ruff check src/ tests/

# Type check
mypy src/

# Boot the system (default: LeadForge, supervised mode)
python -m src.bootstrap

# Boot with options
python -m src.bootstrap --company leadforge --mode supervised --dashboard --loop

# Boot a different company
python -m src.bootstrap --company dealforge
```

## Architecture

### Platform Layer (`src/core/`, `src/config/`, `src/mcp/`, `src/workflows/`, `src/dashboard/`)

- **`src/core/agent_invoker.py`** — `AgentInvoker` is the universal entry point. Contains `AgentConfig`, `AgentRegistry`, `AgentTier`, `TaskMetadata`, and delegation helpers.
- **`src/core/claude_client.py`** — Provider-agnostic agentic loop. Uses `LLMClient` protocol (Claude or OpenAI). Includes retry with backoff, session checkpointing, and safe async/sync boundary.
- **`src/core/model_client.py`** — `LLMClient` protocol + `AnthropicClient` + `OpenAIClient`. Shared `MODEL_PRICING` registry. Provider auto-detection from model name.
- **`src/core/hooks.py`** — Seven-check governance chain: budget pre-check → rate limiter → auth checker → cost tracker → compliance checker → Slack notifier → audit logger. `CostTracker.pre_check()` blocks before API calls when budget is near limit.
- **`src/core/session_store.py`** — Agent session persistence + checkpointing. `InMemorySessionStore` (default) and `PostgresSessionStore` (production). Conversations survive crashes.
- **`src/core/database.py`** — Multi-tenant database layer. Connection pooling, tenant context via RLS (`SET app.current_tenant`), Cloud SQL connector support.
- **`src/core/redis_rate_limiter.py`** — Distributed rate limiting via Redis. Atomic INCR + EXPIRE. Falls back to in-memory when Redis unavailable.
- **`src/core/secrets.py`** — GCP Secret Manager with caching + env var fallback. Per-tenant API key retrieval.
- **`src/mcp/custom_tools.py`** — In-process tools: `EventBus`, `HITLGateway`, `KnowledgeBase`, `MetricsStore`. `CompanySystem` auto-detects PostgreSQL vs in-memory.
- **`src/mcp/persistence.py`** — PostgreSQL-backed: `PostgresEventBus`, `PostgresKnowledgeBase`, `PostgresMetricsStore`, `PostgresAuditWriter`.
- **`src/mcp/tool_executor.py`** — Routes tool calls: `company__*` → in-process, `mcp__*` → MCP servers. Registers MCP tool schemas dynamically.
- **`src/mcp/server_manager.py`** — MCP server lifecycle: connect, discover tools via `list_tools()`, disconnect. Graceful degradation.
- **`src/workflows/definitions.py`** — DAG workflow engine with parallel dispatch via `asyncio.gather()`. `TaskGraphBuilder` fluent API.
- **`src/dashboard/app.py`** — Flask REST API + HTML dashboard. Auth middleware (Firebase JWT + API keys). Tenant management endpoints.
- **`src/bootstrap.py`** — Boot sequence: DB → MCP servers → tool executor → LLM client → hook chain → agent registry → knowledge base → workflow engine → executives → standing swarms → dashboard → main loop.

### SaaS Layer (`src/api/`, `src/billing/`)

- **`src/api/auth.py`** — Firebase Auth (JWT), API key auth, RBAC (Admin/Operator/Viewer).
- **`src/api/tenants.py`** — Tenant CRUD, onboarding, plan management, user management.
- **`src/billing/plans.py`** — 4 tiers (Trial/Starter/Growth/Enterprise), usage enforcement, overage rates.
- **`src/billing/stripe_billing.py`** — Stripe subscriptions, metered billing, webhook handling, customer portal.

### Company Packages (`src/companies/<company_id>/`)

Each company provides: `agent_configs.py`, `workflows.py`, `knowledge.py`, `config.yaml`, `demo.py`.

### Agent Hierarchy

Three-tier delegation model:
- **Tier 1 (Executive):** CEO/COO/CFO — use Opus, can delegate to Tier 2
- **Tier 2 (Department Leads):** use Opus, can delegate to Tier 3
- **Tier 3 (Workers):** use Sonnet/Haiku, **cannot spawn sub-agents**

### Infrastructure

- `infrastructure/database/schema.sql` — Multi-tenant PostgreSQL schema with RLS. 12 tables including `tenants`, `tenant_users`, `usage_records`.
- `infrastructure/terraform/gcp/main.tf` — Cloud SQL, Memorystore Redis, Cloud Run, VPC, Secret Manager, Cloud Storage, budget alerts.
- `infrastructure/docker/Dockerfile` — Production container.
- `infrastructure/docker/cloudbuild.yaml` — CI/CD: test → build → push → deploy to Cloud Run.

### Key Conventions

- **Multi-tenancy:** All tables have `tenant_id` + RLS policies. `DatabaseClient.tenant(id)` sets session variable.
- **Graceful degradation:** No API key → simulation. No DB → in-memory. No Redis → in-memory rate limiting. No MCP servers → "not connected" errors.
- **Multi-model:** Model name prefix determines provider: `claude-*` → Anthropic, `gpt-*`/`o3-*` → OpenAI.
- **`asyncio_mode = "auto"`** in pytest config.

## Domain Context (LeadForge)

- Lead scoring uses BANT framework; SQL threshold is score ≥70 with ≥2 qualification signals
- Maximum 50 outreach emails per SDR per day per client
- CAN-SPAM and GDPR compliance required for all outreach
- Financial thresholds: <$1K dept lead, $1K-$5K CFO, $5K-$10K CEO, >$10K human board
- Strict per-client data isolation — no cross-client data sharing
