# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**Helios OS v3.1** — An agentic harness that governs AI agents across nine framework adapters (Helios OS, CrewAI, Google ADK, LangChain, OpenClaw, Sandbox, Anthropic SDK, Anthropic Managed, OpenAI Agents) with five execution lifecycles and a Next.js dashboard.

**Key distinction:** Helios OS is the *framework* (the operating system). Agents are the *programs* that run inside it. The framework provides scheduling, tool execution, LLM routing, persistence, and monitoring. Agents define what work gets done.

Example workloads ship in `examples/` (~36 directories). A handful of "gold-standard" pairs (e.g. `codebase-guardian`, `content-ops`, `drive-security-auditor`, `sre-command-center`, `sre-gcp-auditor`) include both a governed `agent.py` and an ungoverned `agent_raw.py` for side-by-side comparison; most others ship only the governed `agent.py`. They are example workloads, not the framework itself.

**Repo:** `forgeos` on GitHub, default branch `main`.

## Commands

```bash
# Install (Python 3.11+)
pip install -e ".[dev]"

# Run all tests (~109 test files)
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

# Local dev via Makefile (Postgres container + backend + dashboard)
make start          # bring up pg -> backend (:5000) -> dashboard (:3000)
make stop | make status | make logs | make reset   # lifecycle / debugging
make migrate        # apply pending SQL migrations (idempotent)
make mc-platform    # boot lightweight in-memory platform on :5099 (Mission Control)

# MCP server for Claude Code / Cursor (registered in .mcp.json)
# Talks to a running Helios OS API via FORGEOS_URL / FORGEOS_API_KEY
python3 tools/forgeos-mcp-server.py
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
| Helios OS | `stacks/forgeos/adapter.py` | Native agentic loop | — |
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

**AgentOS kernel & admission** (policy decision point for every meaningful action) — now a `kernel/` package (BSL-licensed); the flat `syscall.py`/`capabilities.py`/`process.py`/`checkpoint.py` in `src/platform/` are thin re-export shims into it:
- `kernel/_facade.py` — Kernel facade: admission, permissions, budgets, policies, data boundaries
- `kernel/_syscall.py` — **Unified admission pipeline**: identity → capability → quota/budget → policy → boundary → dispatch → audit. Opt-in via `FORGEOS_SYSCALL_PIPELINE=1`; otherwise the legacy `src/core/hooks.py` chain runs.
- `kernel/_capabilities.py` — Opaque capability tokens (runtime grants with expiry + revocation; positive authority that short-circuits ACL checks)
- `kernel/_process.py` — First-class `AgentProcess` table: stable PID, unified phase machine, resource accounting (tokens, USD, tool calls, wallclock)
- `kernel/_checkpoint.py` — Process checkpoint/restore for preemption + durable resume
- `kernel/_license_stage.py` — Production license enforcement stage. Community Edition swaps the whole package for permissive stubs at `src/platform/kernel_stubs/`.
- `rbac.py`, `namespace_policy.py`, `namespace_admins.py` — role-based access control, per-namespace policy, and namespace admin registry layered on top of admission.

**A2A / A2H & async execution**
- `a2a.py` — Agent-to-agent protocol (addressed calls, ACL checks, cycle detection, depth limits). Human-gated callees are routed to the Redis worker tier rather than run inline.
- `a2a_contracts.py` — Typed request/response contracts for A2A calls
- `a2a_transport.py` — Transport layer for A2A calls (inline vs worker-tier dispatch)
- `a2h.py`, `a2h_chat.py` — **Agent-to-Human protocol**: agents request human approvals/input/chat (see the A2H tool family below)
- `task_queue.py` — Distributed Redis-backed task queue for async A2A jobs and human-gated continuations (PENDING/RUNNING/COMPLETED). Drained by the worker tier (`FORGEOS_RUNTIME_WORKERS`, deployed via `pulumi/components/worker.py`).
- `package_registry.py` — Agent/tool package registry (versioned manifests)
- `durable_event_store.py` — Durable event log backing the event bus and A2A async jobs
- `agent_runs_store.py`, `session_events.py`, `session_event_store.py` — per-run / per-session event persistence
- `memory_store.py`, `scoped_state.py`, `knowledge_loader.py` — agent memory, scoped key/value state, and company knowledge loading
- `env_service.py`, `env_tools.py`, `environments.py`, `pod_dev_tools.py` — per-agent execution environments ("pods") and the dev tools scoped to them
- `fleet_monitor.py`, `rollout.py`, `webhook_dispatcher.py`, `user_store.py`, `conversation_manager.py`, `callbacks.py`, `agent_definitions.py`, `agent_tool.py`, `workflow_agents.py`, `postgres_process_table.py` — fleet status, staged rollouts, outbound webhooks, user accounts, chat conversation state, lifecycle callbacks, and Postgres-backed process/agent tables

**Observability**
- `audit.py` — Records all platform events (hash-chained audit trail). Per-tool-call rows carry a compact, secret-redacted `args` summary so `forgeos logs` shows *what* each tool was invoked with.
- `alerts.py` — Multi-destination alerts (Slack, PagerDuty, log)
- `metrics.py` — Prometheus metrics (~24 instruments: Counters, Gauges, Histograms). Deployment/scrape config (ServiceMonitor, PrometheusRule, Grafana dashboards, runbooks) lives in the top-level `observability/`.

**Developer & integration tools** (always-on, registered in `src/mcp/tool_executor.py`)
- `dev_tools.py` — `shell__exec` (allowlisted binaries, no pipes), `fs__write_file`, `git__commit_push`, `gh__open_pr`. gh/git ride a per-invocation token injected via `_ensure_gh_env` (never `os.environ`).
- `email_tool.py` — `notify__email`: sends via the Gmail API using `FORGEOS_GWS_*` OAuth secrets (refresh-token flow); never echoes secret values.
- `drive_audit_tool.py` — `drive__audit_sharing`: read-only Drive sharing audit (reuses `email_tool` creds).
- `drive_tool.py` — Read/write Drive tools: `drive__read_file`, `drive__create_file`, `drive__update_file`, `drive__find_by_name`, `drive__list_files`. Backs the treasury "Drive-backed reconciliation" demo.
- `credentials.py` — Per-user credential store; `inject_for_invocation()` pulls `forgeos-{kind}-pat-{user_id}` from Secret Manager into the invoke context (write-only; no read-back).

**Two admission paths coexist today.** The legacy 7-check `src/core/hooks.py` chain runs by default. Set `FORGEOS_SYSCALL_PIPELINE=1` to activate the new syscall pipeline at adopted call sites. Both are safe to run; the feature flag controls which path executes.

### 2b. Python SDK (`src/forgeos_sdk/`)

Public-facing Python package for declaring and managing agents:

- `manifest.py` — Pydantic schema for `agent.yaml` (supports `forgeos/v1` flat + `agentos/v1` k8s-style)
- `agent.py` — `Agent` class (declarative) + `AgentBuilder` (fluent) — both compile to `AgentManifest`
- `client.py` — `ForgeOSClient` sync HTTP wrapper
- `kernel.py` — `Kernel` accessor (in-process or remote) for permission checks from agent code
- `cli.py` — `forgeos deploy/list/invoke/validate/undeploy/health` CLI
- `mc_cli.py` — `forgeos mc ...` terminal Mission Control (HTTP client of the main platform API: fleet, HITL/A2H inbox, runs, logs)
- `config_file.py` — Reads/writes `~/.forgeos/server.lock` (server URL + token) shared by the SDK client and the standalone Rust CLI
- `runtime.py` — BSL-licensed agent runtime (worker continuations, per-agent endpoint/api_key routing); `runtime_stub.py` is the permissive Community Edition stub

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
- `mcp/platform_tools.py`, `mcp/custom_tools.py`, `mcp/pubsub_bus.py`, `mcp/persistence.py`, `mcp/providers/` — platform/company tool surfaces, custom tool registration, Pub/Sub event bus binding, and MCP persistence
- `companies/<id>/` — Each provides `agent_configs.py`, `workflows.py`, `knowledge.py`, `config.yaml`, `demo.py`. Two company packs today: **`leadforge`** and **`treasury`** (treasury is the active development pack — five agents in the `treasury` namespace driving the Drive-backed reconciliation demo).
- `forgeos_sandbox/runner.py` — In-process sandbox runner helper (distinct from the `stacks/sandbox/` adapter)

### Dashboard

Next.js 15 + React 19 + Tailwind CSS in `dashboard/`. Talks to FastAPI backend (~70 endpoints). OpenAI-inspired dark theme.

### Infrastructure

- `pulumi/` — **Pulumi IaC (top-level)**, the canonical deployment path. The stack (`gcp_stack.py` / `local_stack.py`) is a "lean GCP stack": the platform-api, dashboard, and Redis-queue **worker tier** run on **Cloud Run**, agent execution environments on **GKE Autopilot**, plus Cloud SQL, Memorystore (Redis), Secret Manager, identity, and networking. Components live in `pulumi/components/` (`platform_api.py`, `dashboard.py`, `worker.py`, `gke.py`, `exec_environments.py`, `data.py`, `network.py`, `identity.py`, `secrets.py`, `migrations.py`, `mcp_server.py`, `registry.py`, `agent_local.py`). (The earlier per-agent autoscaling + Mission Control machinery was removed in `de44f725`.)
- `infrastructure/docker/` + top-level `docker-compose.yaml` — Postgres + Redis + API + dashboard for local dev
- `infrastructure/database/` — ~22 SQL migrations (001-020+; covers hitl_approvals, session events/messages, process table, audit log, agent_runs, license enforcement, execution_tier, credentials, policies, scoped_secrets, namespaces, gateway, local_users). Apply with `make migrate`.
- `infrastructure/terraform/` — legacy single `main.tf`; Pulumi is the active path.
- `observability/` — Prometheus scrape config, Grafana dashboards, alert rules, and SEV runbooks wrapping `src/platform/metrics.py`
- `.github/workflows/` — CI: test -> build -> push to GHCR

> **Note:** The standalone `mission-control/` web app, `infrastructure/terraform/gcp/`, the `deploy/k8s/` Kustomize manifests, and `forgeos-lens-seed/` were removed — don't reference them. Operator actions now go through the dashboard, the `forgeos mc` terminal CLI (`src/forgeos_sdk/mc_cli.py`), or the FastAPI API directly.

### Operator console & docs

- `a2h/` — Reference implementation of the Agent-to-Human protocol (typed approval/choice/free-text/confirmation responses).
- `forgeos mc` (terminal Mission Control, `src/forgeos_sdk/mc_cli.py`) — CLI against the main platform API for fleet status, HITL/A2H inbox, runs, and logs. Pair with `make mc-platform` (boots an in-memory platform on :5099).
- `tools/forgeos-mcp-server.py` — Self-contained MCP server exposing Helios OS to Claude Code / Cursor (registered in `.mcp.json`); kept in sync with the importable `src/forgeos_mcp` package.
- `docs/` + `mkdocs.yml` — MkDocs Material documentation site (`mkdocs build`): guides, SDK reference, architecture, protocols, operations, runbooks.

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
- **3-tier hierarchy** (Helios OS stack): Executives (Opus) -> Department Leads (Opus) -> Workers (Sonnet/Haiku)
- **Multi-model:** `claude-*` -> Anthropic, `gpt-*`/`o3-*` -> OpenAI (auto-detected from model prefix)

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

### A2H (Agent-to-Human) Tool Family

Agents request human input/approval (HITL) via a parallel tool family backed by `src/platform/a2h.py` / `a2h_chat.py`:

- `human__ask(...)` — ask a human a question / request approval, block for the answer
- `human__notify(...)` — fire-and-forget notification to a human
- `human__check(...)` — poll whether a pending `human__ask` has been answered
- `human__chat(...)` / `human__chat_check(...)` / `human__chat_close(...)` — open, poll, and close an interactive human chat thread
- `human__list_available(...)` — discover reachable humans

Human-gated A2A callees are dispatched to the Redis worker tier (not run inline) so the calling run can be checkpointed and resumed once the human responds.

## Key Conventions

- **Multi-tenancy:** All DB tables have `tenant_id` + RLS. `DatabaseClient.tenant(id)` sets session context via `set_config('app.current_tenant', ...)`.
- **Graceful degradation:** No API key -> simulation. No DB -> in-memory. No Redis -> in-memory. No MCP -> "not connected". No SDK -> platform fallback.
- **Two admission paths coexist:** legacy `src/core/hooks.py` chain (default) and `src/platform/syscall.py` pipeline (`FORGEOS_SYSCALL_PIPELINE=1`). Both safe; flag decides which runs.
- **`.env` for secrets** — never committed.
- **`agents/` directory** is gitignored — personal/shared agent configs live there at runtime.
- **Audit trail is hash-chained** — never mutate past audit records; only append.

## License

Dual-licensed: BSL 1.1 (kernel at `src/platform/kernel/`, runtime at `src/forgeos_sdk/runtime.py`) + Apache 2.0 (adapters, SDK client libs, examples, docs). Community Edition: full Apache 2.0 with permissive stubs at `src/platform/kernel_stubs/`.
