# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**ForgeOS v3.1** — An agentic harness that governs AI agents across nine framework adapters (ForgeOS, CrewAI, Google ADK, LangChain, OpenClaw, Sandbox, Anthropic SDK, Anthropic Managed, OpenAI Agents) with five execution lifecycles and a Next.js dashboard.

**Key distinction:** ForgeOS is the *framework* (the operating system). Agents are the *programs* that run inside it. The framework provides scheduling, tool execution, LLM routing, persistence, and monitoring. Agents define what work gets done.

Eight gold-standard examples ship in `examples/` — each with a governed `agent.py` and an ungoverned `agent_raw.py` for side-by-side comparison. They are example workloads, not the framework itself.

**Repo:** `forgeos` on GitHub, default branch `main`.

## Commands

```bash
# Install (Python 3.11+)
pip install -e ".[dev]"

# Run all tests (~1256 tests, 78 files)
PYTHONPATH=. python3 -m pytest

# Run a single test file / pattern
PYTHONPATH=. python3 -m pytest tests/test_platform_executor.py
PYTHONPATH=. python3 -m pytest -k "test_deploy"

# Lint / type check
ruff check src/ tests/
mypy src/

# Boot the platform (dev: no auth, in-memory DB)
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000

# Boot the platform (production-shaped: company pack + scheduler loop)
PYTHONPATH=. python3 -m src.bootstrap --company leadforge --dashboard --loop --port 5000

# Boot with AgentOS syscall pipeline enabled (opt-in)
FORGEOS_SYSCALL_PIPELINE=1 PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000

# Next.js dashboard (separate terminal)
cd dashboard && npm install && npm run dev

# CLI (installed as console script by pyproject.toml)
forgeos deploy agent.yaml          # validate + POST to /api/platform/agents
forgeos list | forgeos invoke <id> "prompt" | forgeos undeploy <id> | forgeos health
```

Notes:
- `PYTHONPATH=.` is required because `stacks/` is a top-level package alongside `src/`.
- Use `python3`, not `python` — macOS ships without a `python` symlink.
- `asyncio_mode = "auto"` is set in `pyproject.toml`, so async tests don't need `@pytest.mark.asyncio`.

## Three-Layer Architecture

### 1. Stack Adapters (`stacks/`)

`AgentStackAdapter` ABC in `stacks/base.py`. Nine implementations:

| Adapter | File | Runtime | Fallback |
|---------|------|---------|----------|
| ForgeOS | `stacks/forgeos/adapter.py` | Native agentic loop | — |
| CrewAI | `stacks/crewai/adapter.py` | CrewAI SDK (Crew.kickoff) | Platform loop |
| ADK | `stacks/adk/adapter.py` | Google ADK Runner | Platform loop |
| LangChain | `stacks/langchain/adapter.py` | LangChain AgentExecutor | Platform loop |
| OpenClaw | `stacks/openclaw/adapter.py` | HTTP gateway subprocess | Platform loop |
| Sandbox | `stacks/sandbox/adapter.py` | Docker container sandbox | Platform loop |
| Anthropic SDK | `stacks/anthropic_agent/adapter.py` | Anthropic tool_use loop | Platform loop |
| Anthropic Managed | `stacks/anthropic_managed/adapter.py` | Anthropic managed agents | Platform loop |
| OpenAI Agents | `stacks/openai_agents/adapter.py` | OpenAI Agents SDK | Platform loop |

Each provides: `create_agent()`, `invoke()`, `start_loop()`, `stop()`, `scaffold_files()`.

### 2. Platform Layer (`src/platform/`)

Stack-agnostic orchestration shared by all agents. Grouped roughly by responsibility:

**Orchestration & routing**
- `registry.py` — Universal agent registry (query by stack/type/owner/department/**namespace**)
- `executor.py` — Central dispatcher: deploy, invoke, wire execution lifecycle, recover
- `scheduler.py` — Cron-based scheduling for scheduled agents
- `triggers.py` — Trigger definitions consumed by scheduler / event bus
- `event_bus.py` — Pub/sub for event-driven agents
- `llm_router.py` — Routes to Anthropic/OpenAI, retry with backoff, failover, streaming
- `agentic_loop.py` — LLM -> tool_use -> execute -> tool_result -> LLM loop (sync + streaming)
- `skill_registry.py` — Registered skills catalogue callable from agents
- `mcp_registry.py` — Platform-level MCP binding index

**Persistence & state**
- `persistence.py` — Generic store abstraction backing registry/process tables
- `client_store.py` — Per-client connection / configuration store

**AgentOS kernel & admission** (policy decision point for every meaningful action)
- `kernel.py` — Kernel facade: admission, permissions, budgets, policies, data boundaries
- `syscall.py` — **Unified admission pipeline**: identity → capability → quota/budget → policy → boundary → dispatch → audit. Opt-in via `FORGEOS_SYSCALL_PIPELINE=1`; otherwise the legacy `src/core/hooks.py` chain runs.
- `capabilities.py` — Opaque capability tokens (runtime grants with expiry + revocation; positive authority that short-circuits ACL checks)
- `process.py` — First-class `AgentProcess` table: stable PID, unified phase machine, resource accounting (tokens, USD, tool calls, wallclock)
- `checkpoint.py` — Process checkpoint/restore for preemption + durable resume

**A2A & packaging**
- `a2a.py` — Agent-to-agent protocol (addressed calls, ACL checks, cycle detection, depth limits)
- `a2a_contracts.py` — Typed request/response contracts for A2A calls
- `package_registry.py` — Agent/tool package registry (versioned manifests)
- `durable_event_store.py` — Durable event log backing the event bus and A2A async jobs

**Observability**
- `audit.py` — Records all platform events (hash-chained audit trail). Per-tool-call rows carry a compact, secret-redacted `args` summary so `forgeos logs` shows *what* each tool was invoked with.
- `alerts.py` — Multi-destination alerts (Slack, PagerDuty, log)
- `metrics.py` — Prometheus metrics (14 families)

**Developer & integration tools** (always-on, registered in `src/mcp/tool_executor.py`)
- `dev_tools.py` — `shell__exec` (allowlisted binaries, no pipes), `fs__write_file`, `git__commit_push`, `gh__open_pr`. gh/git ride a per-invocation token injected via `_ensure_gh_env` (never `os.environ`).
- `email_tool.py` — `notify__email`: sends via the Gmail API using `FORGEOS_GWS_*` OAuth secrets (refresh-token flow); never echoes secret values.
- `drive_audit_tool.py` — `drive__audit_sharing`: read-only Drive sharing audit (reuses `email_tool` creds).
- `credentials.py` — Per-user credential store; `inject_for_invocation()` pulls `forgeos-{kind}-pat-{user_id}` from Secret Manager into the invoke context (write-only; no read-back).

**Two admission paths coexist today.** The legacy 7-check `src/core/hooks.py` chain runs by default. Set `FORGEOS_SYSCALL_PIPELINE=1` to activate the new syscall pipeline at adopted call sites. Both are safe to run; the feature flag controls which path executes.

### 2b. Python SDK (`src/forgeos_sdk/`)

Public-facing Python package for declaring and managing agents:

- `manifest.py` — Pydantic schema for `agent.yaml` (supports `forgeos/v1` flat + `agentos/v1` k8s-style)
- `agent.py` — `Agent` class (declarative) + `AgentBuilder` (fluent) — both compile to `AgentManifest`
- `client.py` — `ForgeOSClient` sync HTTP wrapper
- `kernel.py` — `Kernel` accessor (in-process or remote) for permission checks from agent code
- `cli.py` — `forgeos deploy/list/invoke/validate/undeploy/health` CLI
- `config_file.py` — Reads/writes `~/.forgeos/server.lock` (server URL + token) shared by the SDK client and the standalone Rust CLI

### 3. Core + Companies (`src/core/`, `src/companies/`, `src/mcp/`)

- `agent_invoker.py` — Legacy agent orchestration (3-tier hierarchy)
- `claude_client.py` — Provider-agnostic agentic loop (pre-platform layer)
- `model_client.py` — `LLMClient` protocol + Anthropic/OpenAI implementations
- `hooks.py` — Legacy 7-check governance chain (budget, rate limit, auth, cost, compliance, Slack, audit). Runs by default; being migrated to `src/platform/syscall.py`.
- `database.py` — Multi-tenant PostgreSQL with RLS, connection pooling
- `session_store.py` — In-memory or PostgreSQL session persistence
- `mcp/tool_executor.py` — Routes `mcp__*` to MCP servers, `company__*` to in-process handlers; honors syscall-pipeline adoption when `FORGEOS_SYSCALL_PIPELINE=1`
- `mcp/server_manager.py` — MCP server lifecycle (connect, discover tools, disconnect)
- `mcp/client_mcp_manager.py` — Per-client MCP connections with LRU eviction
- `companies/<id>/` — Each provides `agent_configs.py`, `workflows.py`, `knowledge.py`, `config.yaml`, `demo.py`
- `forgeos_sandbox/runner.py` — In-process sandbox runner helper (distinct from the `stacks/sandbox/` adapter)

### Dashboard

Next.js 15 + React 19 + Tailwind CSS in `dashboard/`. Talks to FastAPI backend (~70 endpoints). OpenAI-inspired dark theme.

### Infrastructure

- `pulumi/` — **Pulumi IaC (top-level)**: GKE Autopilot for agent workloads, Cloud SQL + Memorystore + Pub/Sub, identity, networking, observability, secrets (`pulumi/components/`)
- `infrastructure/docker/` — Dockerfile + docker-compose (Postgres + Redis + API)
- `infrastructure/database/` — 5 SQL migrations (001-005)
- `infrastructure/terraform/gcp/` — Cloud SQL, Redis, Cloud Run, VPC, Secret Manager
- `deploy/k8s/` — Kubernetes manifests with Kustomize overlays (dev/staging/prod)
- `.github/workflows/` — CI: test -> build -> push to GHCR

### Standalone Rust CLI (separate repo)

The single-binary `forgeos` CLI is **not** in this repo — it was extracted to
[`antonibergas-hue/forgeos-cli`](https://github.com/antonibergas-hue/forgeos-cli)
(`cargo build --release`). Commands: `health`, `deploy`, `list [--json]`,
`describe`, `invoke` (fire-and-return by default, `--wait` to block), `logs
[--follow]`, `undeploy`. It is distinct from the in-repo Python SDK CLI
(`src/forgeos_sdk/cli.py`).

## Agent Model

- **5 execution types:** always_on, scheduled, event_driven, reflex, autonomous
- **3 ownership types:** personal, shared, client
- **Namespaces** (AgentOS v2): k8s-style logical isolation (`sales-team`, `legal`, `operations`)
- **3-tier hierarchy** (ForgeOS stack): Executives (Opus) -> Department Leads (Opus) -> Workers (Sonnet/Haiku)
- **Multi-model:** `claude-*` -> Anthropic, `gpt-*`/`o3-*` -> OpenAI (auto-detected from model prefix)
- **74 deployed agents:** 53 shared + 21 personal across sales, marketing, finance, HR, legal, operations

### Declarative Agent Contracts

Agents declared via `agent.yaml` manifests with k8s-style structure:

```yaml
apiVersion: agentos/v1
kind: AgentContract
metadata: { name, namespace, labels, annotations }
spec:
  runtime: { framework, image }
  lifecycle: { type, replicas, restart_policy, schedule }
  llm: { chat_model, provider }
  capabilities:
    tools: { allowed, denied }
    a2a: { canCall, canBeCalledBy, max_depth }
  boundaries:
    budgets: { daily_usd, per_task_usd }
    data: { allowed_namespaces, pii_policy }
  governance: { human_in_loop, policies, audit_level }
  dependencies: { agents, mcp_servers }
```

### A2A (Agent-to-Agent) Tool Family

When the kernel is running, agents get four new tools:

- `agent__call(namespace, name, task, context, timeout)` — sync call
- `agent__async_call(...)` — returns `job_id`
- `agent__await(job_id)` — wait for async result
- `agent__list_available(namespace, department)` — discover callable peers

ACLs enforced via callee's `spec.capabilities.a2a.canBeCalledBy` at every call.

## Key Conventions

- **Multi-tenancy:** All DB tables have `tenant_id` + RLS. `DatabaseClient.tenant(id)` sets session context via `set_config('app.current_tenant', ...)`.
- **Graceful degradation:** No API key -> simulation. No DB -> in-memory. No Redis -> in-memory. No MCP -> "not connected". No SDK -> platform fallback.
- **Two admission paths coexist:** legacy `src/core/hooks.py` chain (default) and `src/platform/syscall.py` pipeline (`FORGEOS_SYSCALL_PIPELINE=1`). Both safe; flag decides which runs.
- **`.env` for secrets** — never committed.
- **`agents/` directory** is gitignored — personal/shared agent configs live there at runtime.
- **Audit trail is hash-chained** — never mutate past audit records; only append.

## License

Dual-licensed: BSL 1.1 (kernel at `src/platform/kernel/`, runtime at `src/forgeos_sdk/runtime.py`) + Apache 2.0 (adapters, SDK client libs, examples, docs). Community Edition: full Apache 2.0 with permissive stubs at `src/platform/kernel_stubs/`.
