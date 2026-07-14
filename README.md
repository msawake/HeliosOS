> [!NOTE]
> **Open-core mirrors (kept in sync):** the stripped public tree is published to
> **[msawake/HeliosOS](https://github.com/msawake/HeliosOS)** (canonical) and
> **[makingscience-awake/forgeos](https://github.com/makingscience-awake/forgeos)** (mirror).
> Both `main` branches carry the same commit history and contributors graph.

# Helios OS

> **Created by [Jose Antonio Martinez (jama)](https://github.com/jamartinezaguilar2025)** — first commit [`809d2d8`](https://github.com/makingscience-awake/heliosos-enterprise/commit/809d2d8a87a814bacc9c584f7dd92b6c41fa4fc0) (*ForgeOS v3.1 — Multi-Stack AI Agent Platform*, May 2026). See [CONTRIBUTORS.md](CONTRIBUTORS.md) — `helioscode` is an AI coding agent (like Claude Code), not the creator or [github.com/helioscode](https://github.com/helioscode).

**The agentic harness.** Control what your agents do — on any framework, without changing their code. Deploy, orchestrate, and govern agents across 9 framework adapters with a kernel, syscall pipeline, runtime SDK, and inter-agent protocols.

Helios OS is the **harness**. Agents are the **processes** that run inside it.

```
Helios OS (the operating system)
  Kernel:    admission control, permissions, budgets, policies, data boundaries
  Syscall:   identity -> capability -> quota -> policy -> boundary -> dispatch -> audit
  Runtime:   SDK that agents use to interact with the kernel at runtime
  Platform:  registry, executor, scheduler, event bus, LLM routing, agentic loop
  Protocols: A2A (agent-to-agent), A2H (agent-to-human), MCP (agent-to-tool)

Agents (the processes)
  Defined by: manifest (name, framework, lifecycle, tools, boundaries)
  Deployed via: API, CLI, or SDK
  Run on: one of 9 framework adapters (Helios OS, CrewAI, ADK, LangChain/LangGraph, OpenClaw, Sandbox, Anthropic SDK, Anthropic Managed, OpenAI Agents)
  Governed by: kernel enforcement on every tool call, budget check, and agent call
```

---

## Quick Start

### Option A — Docker (recommended for a first test drive)

Requires only Docker. From a fresh clone:

```bash
git clone https://github.com/msawake/HeliosOS.git
cd HeliosOS
docker compose up
```

This boots PostgreSQL (pgvector), Redis, and the platform API on http://localhost:5000 with zero configuration — API auth is disabled for local testing, and without API keys all LLM calls return simulated responses, so you can exercise the full deploy/invoke/governance loop for free.

For real model responses:

```bash
cp .env.example .env        # set ANTHROPIC_API_KEY (and/or OPENAI_API_KEY)
docker compose up
```

### Option B — Run on the host

```bash
# Install (Python 3.11+)
pip install -e ".[dev]"

# Configure
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

# Boot the platform
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000

# Start the dashboard — maintained in its own repo (separate terminal)
git clone git@github.com:antonibergas-hue/forgeos-dashboard.git ../forgeos-dashboard
cd ../forgeos-dashboard && npm install && npm run dev
```

Dashboard at http://localhost:3000. API docs at http://localhost:5000/docs.
The dashboard is a standalone Next.js app: [antonibergas-hue/forgeos-dashboard](https://github.com/antonibergas-hue/forgeos-dashboard).

### Install the CLI

The `forgeos` CLI is a single static Rust binary, maintained in its own repo: [antonibergas-hue/forgeos-cli](https://github.com/antonibergas-hue/forgeos-cli).

```bash
git clone https://github.com/antonibergas-hue/forgeos-cli.git
cd forgeos-cli
cargo build --release
sudo cp target/release/forgeos /usr/local/bin/
```

(Prefer Python? `pip install -e .` in this repo installs an equivalent `forgeos` CLI from the SDK — use `FORGEOS_API_URL` instead of `FORGEOS_REMOTE` below.)

### Deploy your first agent

Point the CLI at your local stack and deploy:

```bash
export FORGEOS_REMOTE=http://localhost:5000

forgeos health

cat > hello.yaml <<'EOF'
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: hello
  description: "A simple test agent"
spec:
  stack: forgeos
  execution_type: reflex
  llm:
    chat_model: claude-sonnet-4-6
    provider: anthropic
  system_prompt: |
    You are a friendly hello-world agent. Keep replies short.
EOF

forgeos deploy hello.yaml      # prints the agent id, e.g. "Deployed agent: 1c5b3f3d-93f"
forgeos list
forgeos invoke <agent-id> "Hello, what can you do?" --wait
forgeos logs <agent-id>
```

More example manifests (all five lifecycles, multiple stacks) live in [`examples/`](examples/) — try `forgeos deploy examples/forgeos/hello-world.yaml`.

Agents can also be deployed straight via the REST API:

```bash
curl -s -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello-agent",
    "stack": "forgeos",
    "execution_type": "reflex",
    "description": "A simple test agent",
    "chat_model": "claude-sonnet-4-6"
  }' | python3 -m json.tool
```

---

## Architecture

```
    +===================================================================+
    |                       KERNEL (policy core)                        |
    |                                                                   |
    |  +-----------+  +-----------+  +-----------+  +-----------+       |
    |  | Admission |  |Permission |  |  Budget   |  |  Policy   |       |
    |  |Controller |  | Manager   |  |  Manager  |  |  Engine   |       |
    |  +-----------+  +-----------+  +-----------+  +-----------+       |
    |  +-----------+  +-----------+  +-----------+  +-----------+       |
    |  |Capability |  |   Data    |  |  Process  |  |Checkpoint |       |
    |  |  Tokens   |  |Boundaries |  |   Table   |  | / Restore |       |
    |  +-----------+  +-----------+  +-----------+  +-----------+       |
    |                                                                   |
    |  +-------------------------------------------------------------+  |
    |  | Syscall: identity -> capability -> quota -> policy ->       |  |
    |  |          boundary -> dispatch -> audit                      |  |
    |  +-------------------------------------------------------------+  |
    +==================+============================+===================+
                       |                            ^
                       v                            | runtime.check_tool()
    +------------------+-------------------+    +---+-------------------+
    |             PROTOCOLS                |    |    SDK RUNTIME        |
    |                                      |    |  (agent-side API)    |
    |  +-----------+ +-----------+ +-----+ |    |                      |
    |  |    A2A    | |    A2H    | | MCP | |    |  check_tool()        |
    |  | agent-to- | | agent-to- | |agent| |    |  get_budget()        |
    |  |  agent    | |  human    | | -to-| |    |  call_agent()        |
    |  |           | |           | |tool | |    |  ask_human()         |
    |  +-----------+ +-----------+ +-----+ |    |  save_checkpoint()   |
    +------------------+-------------------+    |  emit_metric()       |
                       |                        |  log_audit()         |
    +------------------v-------------------+    +----------+-----------+
    |          PLATFORM SERVICES           |               |
    |                                      |               |
    |  +---------+ +---------+ +---------+ |               |
    |  |Registry | |Executor | |Scheduler| |               |
    |  +---------+ +---------+ +---------+ |               |
    |  +---------+ +---------+ +---------+ |               |
    |  |  LLM    | | Agentic | |  Audit  | |               |
    |  | Router  | |  Loop   | |  Log    | |               |
    |  +---------+ +---------+ +---------+ |               |
    +------------------+-------------------+               |
                       |                                   |
    +------------------v-----------------------------------+--------+
    |                      STACK ADAPTERS (9)                        |
    |                                                               |
    |  +---------+ +--------+ +------+ +----------+ +---------+    |
    |  | Helios OS | | CrewAI | |  ADK | | OpenClaw | | Sandbox |    |
    |  | (native)| |(crews) | |(Goog)| |(gateway) | | (Docker)|    |
    |  +---------+ +--------+ +------+ +----------+ +---------+    |
    |  +-------------+ +--------------+ +--------------+           |
    |  | Anthropic    | | Anthropic    | | OpenAI       |           |
    |  | Agent SDK    | | Managed      | | Agents SDK   |           |
    |  |(PreToolUse)  | |(REST API)    | |(on_tool_start)|          |
    |  +-------------+ +--------------+ +--------------+           |
    |  +--------------+                                             |
    |  | LangChain /  |                                             |
    |  | LangGraph    |                                             |
    |  |(on_tool_start)|                                            |
    |  +--------------+                                             |
    |                                                               |
    |        each adapter gets runtime bound per invocation          |
    +----------------------------------------------------------------+
                       |
          +------------v------------+
          |  FastAPI + Dashboard     |
          +-------------------------+
```

---

## Kernel

The kernel is the policy decision point for every meaningful action. No tool call, agent invocation, or budget spend bypasses it.

| Subsystem | What It Does |
|-----------|-------------|
| **AdmissionController** | Validates agent contracts before deploy |
| **PermissionManager** | Runtime tool + A2A ACL checks |
| **BudgetManager** | Per-task and daily USD enforcement |
| **PolicyEngine** | Declarative rule evaluation (Rego, JSON) |
| **DataBoundaryManager** | Namespace isolation + PII policy |
| **CapabilityManager** | Opaque runtime grants with expiry + revocation |
| **AuditRecorder** | Immutable, hash-chained decision trail |

Every check returns a uniform `KernelDecision(allowed, reason, metadata)`.

### Syscall Pipeline

All kernel checks flow through a 7-stage pipeline:

```
identity -> capability -> quota/budget -> policy -> boundary -> dispatch -> audit
```

Each stage can short-circuit (deny) or pass through. The pipeline replaces the legacy hook chain and is the only admission path for new work.

### Process Table

Agents are first-class processes with:
- Stable PID, phase machine (Pending -> Running -> Succeeded/Failed/Quarantined)
- Resource accounting: tokens, USD spent, tool calls, wall-clock time
- Checkpoint/restore for preemption and durable resume

---

## SDK Runtime — 17 Methods No Other Framework Has

The Runtime is the agent-side interface to the kernel — like `libc` for UNIX processes. Every agent gets a `runtime` singleton. The same API works in-process (~0.1ms) or via HTTP (~50ms) to a remote kernel.

```python
from forgeos_sdk import runtime
```

### Policy Checks

| Method | What It Does | Who Else Has This? |
|---|---|---|
| `check_tool(name, input)` | Check permission before calling a tool | **Nobody** does proactive checks |
| `check_a2a(namespace, name)` | Check if allowed to call another agent | Only Helios OS |
| `check_data(namespace)` | Check namespace boundary access | Only Helios OS |
| `syscall(verb, target, args)` | Run through full 7-stage admission pipeline | **Nobody** — the Linux syscall model for agents |

### Budget Management

| Method | What It Does | Who Else Has This? |
|---|---|---|
| `budget()` | Query current spend, limits, remaining | **Nobody** — no budget primitives elsewhere |
| `reserve(cost)` | Reserve budget before expensive operation | **Nobody** — two-phase commit for money |
| `commit(ticket, actual_cost)` | Finalize reservation with actual cost | **Nobody** |
| `release(ticket)` | Release unused reservation | **Nobody** |

### Process Lifecycle

| Method | What It Does | Who Else Has This? |
|---|---|---|
| `checkpoint(state)` | Save agent state for crash recovery | LangGraph only; **nobody else** |
| `last_checkpoint()` | Resume from saved state after crash | LangGraph only |
| `pending_signals()` | Check for SIGTERM/SIGSTOP/SIGEVICT | **Nobody** — agents elsewhere just get killed |
| `signal(pid, name, reason)` | Send signal to another agent | **Nobody** |

### Delegation & Introspection

| Method | What It Does | Who Else Has This? |
|---|---|---|
| `request_capability(target, verb, ttl)` | Issue time-limited delegation token | **Nobody** — OS capability security for agents |
| `revoke_capability(token_id)` | Revoke a delegation token | **Nobody** |
| `ask_human(question, options, deadline)` | Ask human for approval with typed response | Strands has basic `interrupt()`; this is richer |
| `notify_human(message, priority)` | Alert human without blocking | Only Helios OS |
| `contract()` | Read own deployment contract (budget, policies) | **Nobody** — agents can't see their own constraints |
| `process()` | Read own resource usage (tokens, dollars, tool calls) | **Nobody** |
| `audit(event, details)` | Record custom event to hash-chained audit trail | Only Helios OS |

### Example

```python
from forgeos_sdk import runtime

# Check permission before calling a tool
decision = await runtime.check_tool("send_email", {"to": "customer@..."})
if decision.denied:
    print(f"Blocked: {decision.reason}")

# Reserve budget before expensive LLM call
ticket = await runtime.reserve(estimated_cost_usd=0.05)
try:
    result = await expensive_llm_call()
    await runtime.commit(ticket, actual_cost_usd=0.03)
except Exception:
    await runtime.release(ticket)

# Check for operator signals between work phases
signals = await runtime.pending_signals()
if "SIGTERM" in signals:
    await runtime.checkpoint({"step": current_step})
    return  # exit gracefully

# Ask human for approval
response = await runtime.ask_human(
    namespace="human", name="sales-lead",
    question="Approve this email?",
    response_type="choice",
    options=[{"label": "Approve"}, {"label": "Reject"}],
)
```

The runtime is bound per-invocation via `contextvars` — each concurrent agent sees its own identity and budget.

---

## Remote Kernel (HTTP Mode)

Agents can run on **separate Cloud Run instances** or VMs, governed by Helios OS via HTTP. The same `runtime.check_tool()` API works identically — the SDK auto-detects whether to make a direct Python call or an HTTP request.

```
┌──────────────────────────────┐     ┌──────────────────────────────┐
│  Cloud Run: Agent Fleet       │     │  Cloud Run: Helios OS Control │
│                               │     │  Plane (kernel lives here)    │
│  ┌─────┐ ┌─────┐ ┌─────┐    │HTTP │                               │
│  │ADK  │ │CrewAI│ │Lang │    │────▶│  POST /kernel/check-tool      │
│  │agent│ │agent │ │Chain│    │     │  POST /kernel/check-a2a       │
│  └─────┘ └─────┘ └─────┘    │     │  POST /kernel/usage           │
│                               │     │  GET  /kernel/contract/{id}   │
│  forgeos_sdk installed        │     │                               │
│  FORGEOS_API_URL set          │     │  Kernel → ALLOW / DENY        │
└──────────────────────────────┘     └──────────────────────────────┘
```

```bash
# On the remote agent's Cloud Run:
export FORGEOS_API_URL=https://forgeos-api.example.com
export FORGEOS_API_KEY=fos_sales_xxxx
```

```python
# Agent code — identical to in-process mode:
from forgeos_sdk import runtime
decision = await runtime.check_tool("send_email")
# → HTTP POST to Helios OS control plane → kernel decides → ~50ms
```

200 remote agents × 10 tool calls = 2,000 HTTP requests — trivial for FastAPI, invisible next to 5-30s LLM calls.

---

## Stack Adapters

| Stack | Runtime | SDK Required | Best For |
|-------|---------|-------------|----------|
| **Helios OS** | Native agentic loop | None | Default. Full flexibility, all kernel features |
| **CrewAI** | `Crew.kickoff()` | `pip install crewai` | Role-based multi-agent collaboration |
| **Google ADK** | `Runner.run_async()` | `pip install google-adk` | Google ecosystem, Gemini models |
| **LangChain/LangGraph** | `AgentExecutor` / `ToolNode` | `pip install langchain` | Existing LangChain apps, complex tool chains |
| **OpenClaw** | HTTP gateway subprocess | Node.js + openclaw2 | Markdown-driven, SOUL/HEARTBEAT pattern |
| **Sandbox** | Docker container | Docker | Untrusted code, isolated execution |

**External SDK Adapters** (agents can also run on separate infrastructure, governed by Helios OS kernel via HTTP):

| Stack | Runtime | SDK Required | Governance Hook |
|-------|---------|-------------|----------------|
| **Anthropic Agent SDK** | Claude SDK `query()` | `pip install claude-agent-sdk` | `PreToolUse` hook — one hook gates ALL tools |
| **Anthropic Managed** | Anthropic hosted sandbox | None (REST API) | Session-level gate (no per-tool interception) |
| **OpenAI Agents** | OpenAI Agents SDK `Runner.run()` | `pip install openai-agents` | `AgentHooks.on_tool_start()` hook |
| **LangChain / LangGraph** | `AgentExecutor` / `ToolNode` | `pip install langchain-core` | `ForgeOSKernelCallback` — one `on_tool_start` callback gates ALL tools |

All 9 adapters implement the same `AgentStackAdapter` interface. All adapters have kernel gates — every tool call is checked through `runtime.check_tool()` regardless of which framework runs the agent. External SDK adapters fall back to the platform agentic loop when their SDK is not installed.

The Anthropic, OpenAI, and LangChain adapters support three deployment modes:
- **Mode A** (in-process): agent runs inside Helios OS, kernel check is a direct Python call (~0.1ms)
- **Mode B** (pure): agent runs standalone, no governance
- **Mode C** (remote HTTP): agent runs on separate Cloud Run, kernel checked via HTTP (~50ms)

---

## Protocols

### A2A (Agent-to-Agent)

Agents call other agents across any stack adapter:

```python
agent__call(namespace="sales", name="cfo", task="Analyze Q4 numbers")
agent__async_call(namespace="legal", name="reviewer", task="Review contract")
agent__await(job_id, timeout=120)
agent__list_available(namespace="finance")
```

ACLs declared in manifest (`spec.capabilities.a2a`). Cycle detection, depth limits, and permission checks enforced by the kernel.

### A2H (Agent-to-Human)

Agents ask humans structured questions with deadlines, escalation, and auto-delegation:

```python
human__ask(question="Approve deal?", response_type="choice", options=["Yes", "No"])
human__notify(message="Report generated", channel="slack")
human__check(request_id="req_abc")
human__list_available(namespace="sales")
```

A2H is a companion protocol to A2A and MCP. See the [A2H specification](docs/protocols/a2h-spec.md) and the standalone package in `a2h/`.

### MCP (Agent-to-Tool)

Tools are routed through the MCP tool executor. Tool names follow the `mcp__{server}__{tool}` convention. The executor supports MCP servers, custom in-process handlers, and platform-level tools (A2A, A2H, kernel).

---

## Execution Lifecycles

| Type | Behavior | Example |
|------|----------|---------|
| `always_on` | Runs continuously in a loop | System health monitor |
| `scheduled` | Triggered by cron expression | Daily report (`0 9 * * *`) |
| `event_driven` | Triggered by event bus messages | Alert on `cost.exceeded` |
| `reflex` | On-demand, responds to API/chat | Chat agent, single-turn Q&A |
| `autonomous` | Goal-directed, iterates until done | Research agent with an objective |

---

## Agent Manifests

Agents are declared as YAML contracts (k8s-style):

```yaml
apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: sales-researcher
  namespace: sales-team
  labels: { domain: sales, tier: production }
spec:
  runtime:
    framework: forgeos
  lifecycle:
    type: always_on
    restart_policy: OnFailure
  llm:
    chat_model: claude-sonnet-4-5-20250514
    provider: anthropic
  capabilities:
    tools:
      allowed: [mcp__filesystem__*, company__search_knowledge]
      denied: [shell.exec]
    a2a:
      canCall: [{ namespace: sales-team, agents: [cfo] }]
      max_depth: 3
  boundaries:
    budgets: { daily_usd: 45.00, per_task_usd: 5.00 }
    data: { pii_policy: redact }
  governance:
    human_in_loop:
      - event: email.send
        approvers: [team-lead]
    audit_level: full
```

See [Agent Manifest Reference](docs/reference/agent-manifest.md) for the full schema.

---

## Project Structure

```
.
+-- src/                            # Platform + kernel (134 Python files, ~40K lines)
|   +-- bootstrap.py                # Boot: DB -> MCP -> kernel -> adapters -> API
|   +-- platform/                   # Kernel, syscall, process table, registry, executor,
|   |                               #   scheduler, event bus, LLM router, agentic loop,
|   |                               #   A2A, A2H, capabilities, checkpoints, audit, metrics
|   +-- forgeos_sdk/                # Python SDK: Agent, Runtime, Kernel, Manifest, CLI
|   +-- core/                       # Database, session store, model clients
|   +-- dashboard/                  # FastAPI app (~78 endpoints)
|   +-- billing/                    # Stripe billing, usage enforcement
|   +-- api/                        # Auth (Firebase JWT + API keys), tenants
|   +-- workflows/                  # DAG workflow engine
|   +-- forgeos_sandbox/            # Sandbox runner (Docker container primitives)
|
+-- stacks/                         # Stack adapter layer (9 adapters)
|   +-- base.py                     # AgentStackAdapter ABC, AgentDefinition, enums
|   +-- forgeos/adapter.py          # Native Helios OS adapter
|   +-- crewai/adapter.py           # CrewAI adapter
|   +-- adk/adapter.py              # Google ADK adapter
|   +-- openclaw/adapter.py         # OpenClaw gateway adapter
|   +-- sandbox/adapter.py          # Docker sandbox adapter
|   +-- anthropic_agent/adapter.py  # Anthropic Agent SDK (PreToolUse hook)
|   +-- anthropic_managed/adapter.py # Anthropic Managed Agents (REST API)
|   +-- openai_agents/adapter.py    # OpenAI Agents SDK (on_tool_start hook)
|   +-- langchain/adapter.py        # LangChain/LangGraph (on_tool_start callback)
|   +-- langchain/callback.py       # ForgeOSKernelCallback for Mode C (HTTP)
|
+-- a2h/                            # A2H protocol (standalone package)
|   +-- a2h/                        # Gateway, models, store, channels, registry
|   +-- tests/                      # Protocol conformance tests
|
+-- examples/                       # Example agents per stack (Helios OS, CrewAI, ADK, etc.)
+-- agents/                         # Deployed agent configurations (gitignored)
+-- tests/                          # ~1249 tests across 67 files
+-- docs/                           # Architecture, guides, reference, runbooks, protocols
+-- infrastructure/                 # Docker, Terraform (GCP), database migrations
+-- deploy/                         # Kubernetes manifests (dev/staging/prod)
+-- observability/                  # Prometheus + Grafana dashboards
```

**Extracted into their own repos:** the Rust CLI ([forgeos-cli](https://github.com/antonibergas-hue/forgeos-cli)), the MCP server + tool-execution layer ([forgeos-mcp](https://github.com/antonibergas-hue/forgeos-mcp)), and the Next.js dashboard ([forgeos-dashboard](https://github.com/antonibergas-hue/forgeos-dashboard)).

---

## Graceful Degradation

Helios OS runs with whatever is available:

| Component | Available | Unavailable |
|-----------|-----------|-------------|
| Anthropic/OpenAI API key | Real LLM calls | Simulated responses |
| PostgreSQL | Persistent storage + RLS | In-memory (lost on restart) |
| Redis | Distributed rate limiting | In-memory rate limiting |
| MCP servers | Real tool execution | "Not connected" errors |
| CrewAI/ADK/LangChain/OpenClaw SDK | Native framework execution | Falls back to Helios OS agentic loop |

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Manifesto](docs/manifesto.md) | The agentic harness thesis — UNIX analogies, 17 runtime methods, 9 adapters |
| [Manifesto Summary](docs/manifesto-summary.md) | 5-page overview (PDF available) |
| [Architecture Overview](docs/architecture/overview.md) | OS layers, kernel, platform, adapters |
| [Agentic Loop Integration](docs/architecture/agentic-loop-integration.md) | How the kernel intercepts tools in all 9 frameworks |
| [AgentOS Kernel](docs/architecture/kernel.md) | Admission, permissions, budgets, policies, capabilities |
| [Syscall Pipeline](docs/architecture/kernel.md) | 7-stage admission pipeline |
| [A2A Protocol](docs/architecture/a2a-protocol.md) | Agent-to-agent calling across frameworks |
| [A2H Protocol](docs/protocols/a2h-spec.md) | Agent-to-human interaction protocol |
| [Platform Layer](docs/architecture/platform-layer.md) | Registry, executor, scheduler, event bus, LLM router |
| [Stack Adapters](docs/architecture/stack-adapters.md) | Helios OS, CrewAI, ADK, LangChain, OpenClaw, Sandbox, Anthropic, OpenAI |
| [Python SDK](docs/guides/sdk.md) | Agent, Runtime, Kernel, Manifest, CLI |
| [Agent Manifest](docs/reference/agent-manifest.md) | Full `agent.yaml` schema |
| [API Reference](docs/reference/api-endpoints.md) | All FastAPI endpoints |
| [Creating Agents](docs/guides/creating-agents.md) | 5 lifecycles, 3 ownership types, tools |
| [Quick Start](docs/guides/quickstart.md) | Install, configure, boot, deploy |
| [Google Cloud Deployment](docs/guides/google-cloud-deployment.md) | Cloud Run + Cloud SQL + Secret Manager + CI/CD |

### Examples

| Example | Description |
|---------|-------------|
| [Sales Intelligence](examples/sales-intelligence/) | 6 agents, 3 frameworks, 5 models — full platform demo |
| [SRE Command Center](examples/sre-command-center/) | 7 scenes, 6 agents, 35 runtime controls — incident response, code review, deployment governance |
| [SRE GCP Auditor](examples/sre-gcp-auditor/) | Daily audit of all GCP projects — infrastructure, security, and billing with 10 runtime controls |
| [Drive Security Auditor](examples/drive-security-auditor/) | Daily Google Drive permission scan — 28 controls across 7 phases |
| [Content Ops Pipeline](examples/content-ops/) | Multi-client content production — Gemini produces, Claude reviews (12 controls/piece) |
| [Codebase Guardian](examples/codebase-guardian/) | Always-on GitHub PR reviewer with security scanning (15 controls/iteration) |
| [Competitive Intelligence](examples/competitive-intel/) | Dual-LLM research — Gemini scans, Claude analyzes (13 controls) |
| [SRE Ops Agent](examples/sre-ops-agent/) | Always-on infrastructure monitor with Claude SDK (11 controls/iteration) |

---

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, asyncio, psycopg3
- **Frontend:** Next.js 15, React 19, Tailwind CSS, TypeScript
- **LLM Providers:** Anthropic (Claude), OpenAI (GPT/o3), Google (Gemini)
- **Database:** PostgreSQL 16 with pgvector, Row-Level Security
- **Tools:** Model Context Protocol (MCP) servers
- **Infrastructure:** Docker, Kubernetes, Terraform (GCP), GitHub Actions
- **Monitoring:** Prometheus, Grafana, structured logging

---

## Running Tests

```bash
PYTHONPATH=. python3 -m pytest                                    # All ~1249 tests
PYTHONPATH=. python3 -m pytest tests/test_platform_executor.py    # Single file
PYTHONPATH=. python3 -m pytest -k "test_deploy"                   # By pattern
```

## License

[Business Source License 1.1](LICENSE) — the entire codebase.

- **Individuals** (personal, non-commercial use) and **educational institutions** (teaching, academic research): free to use in production
- **Commercial use** (any use by or on behalf of a company): requires a [commercial license](mailto:licensing@awakeventurestudio.co)
- **Change Date: 2030-05-20** — converts to Apache License 2.0

Copyright 2024-2026 [Awake Venture Studio](https://awakeventurestudio.co), a Making Science Group company.
