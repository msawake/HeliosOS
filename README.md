# ForgeOS

**An operating system for AI agents.** Deploy, orchestrate, and govern agents across five framework adapters with a kernel, syscall pipeline, runtime SDK, and inter-agent protocols.

ForgeOS is the **OS**. Agents are the **processes** that run inside it.

```
ForgeOS (the operating system)
  Kernel:    admission control, permissions, budgets, policies, data boundaries
  Syscall:   identity -> capability -> quota -> policy -> boundary -> dispatch -> audit
  Runtime:   SDK that agents use to interact with the kernel at runtime
  Platform:  registry, executor, scheduler, event bus, LLM routing, agentic loop
  Protocols: A2A (agent-to-agent), A2H (agent-to-human), MCP (agent-to-tool)

Agents (the processes)
  Defined by: manifest (name, framework, lifecycle, tools, boundaries)
  Deployed via: API, CLI, or SDK
  Run on: one of 5 framework adapters (ForgeOS, CrewAI, ADK, OpenClaw, Sandbox)
  Governed by: kernel enforcement on every tool call, budget check, and agent call
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
    |                    STACK ADAPTERS                              |
    |                                                               |
    |  +---------+ +---------+ +---------+ +----------+ +---------+ |
    |  | ForgeOS | |  CrewAI |  |  ADK  |  | OpenClaw | | Sandbox | |
    |  | (native)| | (crews) |  |(Google|  |(gateway) | | (Docker)| |
    |  +----+----+ +----+----+  +---+---+  +----+-----+ +----+----+ |
    |       |           |          |            |             |      |
    |       +-----+-----+----+----+-----+------+------+------+      |
    |             |          |          |              |              |
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

## SDK Runtime

Agent code interacts with the kernel through the Runtime SDK:

```python
from forgeos_sdk import runtime

# Called automatically by the executor on each invocation
# runtime.bind(agent_id, namespace)

# Budget & permissions
remaining = await runtime.get_budget()
decision  = await runtime.check_tool("mcp__gmail__send")

# Agent-to-agent
result = await runtime.call_agent("sales-team", "cfo", task="Q4 analysis")

# Agent-to-human (A2H)
answer = await runtime.ask_human(
    question="Approve the $2.5M deal?",
    response_type="choice",
    options=["Approve", "Reject"],
    priority="high"
)

# Process management
await runtime.save_checkpoint({"progress": "step_3"})
state = await runtime.restore_checkpoint()

# Observability
await runtime.emit_metric("leads_processed", 42)
await runtime.log_audit("deal.approved", {"deal_id": "D-1234"})
```

The runtime is a module-level singleton bound per-invocation via `contextvars`, so each concurrent agent sees its own identity and budget.

---

## Stack Adapters

| Stack | Runtime | SDK Required | Best For |
|-------|---------|-------------|----------|
| **ForgeOS** | Native agentic loop | None | Default. Full flexibility, all kernel features |
| **CrewAI** | `Crew.kickoff()` | `pip install crewai` | Role-based multi-agent collaboration |
| **Google ADK** | `Runner.run_async()` | `pip install google-adk` | Google ecosystem, Gemini models |
| **OpenClaw** | HTTP gateway subprocess | Node.js + openclaw2 | Markdown-driven, SOUL/HEARTBEAT pattern |
| **Sandbox** | Docker container | Docker | Untrusted code, isolated execution |

All adapters implement the same `AgentStackAdapter` interface. All adapters have kernel gates -- every tool call is checked through `runtime.check_tool()` regardless of which framework runs the agent. External SDK adapters fall back to the platform agentic loop when their SDK is not installed.

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

## Quick Start

```bash
# Install (Python 3.11+)
pip install -e ".[dev]"

# Configure
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

# Boot the platform
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000

# Start the dashboard (separate terminal)
cd dashboard && npm install && npm run dev

# Deploy an agent
curl -s -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello-agent",
    "stack": "forgeos",
    "execution_type": "reflex",
    "description": "A simple test agent",
    "chat_model": "claude-sonnet-4-5-20250514"
  }' | python3 -m json.tool

# Or use the CLI
forgeos deploy agent.yaml
forgeos list
forgeos invoke <agent-id> "Hello, what can you do?"
```

Dashboard at http://localhost:3000. API at http://localhost:5000/docs.

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
|   +-- mcp/                        # MCP server manager, tool executor, providers
|   +-- dashboard/                  # FastAPI app (~78 endpoints)
|   +-- billing/                    # Stripe billing, usage enforcement
|   +-- api/                        # Auth (Firebase JWT + API keys), tenants
|   +-- workflows/                  # DAG workflow engine
|   +-- forgeos_sandbox/            # Sandbox runner (Docker container primitives)
|
+-- stacks/                         # Stack adapter layer
|   +-- base.py                     # AgentStackAdapter ABC, AgentDefinition, enums
|   +-- forgeos/adapter.py          # Native ForgeOS adapter
|   +-- crewai/adapter.py           # CrewAI adapter
|   +-- adk/adapter.py              # Google ADK adapter
|   +-- openclaw/adapter.py         # OpenClaw gateway adapter
|   +-- sandbox/adapter.py          # Docker sandbox adapter
|
+-- a2h/                            # A2H protocol (standalone package)
|   +-- a2h/                        # Gateway, models, store, channels, registry
|   +-- tests/                      # Protocol conformance tests
|
+-- dashboard/                      # Next.js 15 + React 19 + Tailwind frontend
+-- examples/                       # Example agents per stack (ForgeOS, CrewAI, ADK, etc.)
+-- agents/                         # Deployed agent configurations (gitignored)
+-- tests/                          # ~1249 tests across 67 files
+-- docs/                           # Architecture, guides, reference, runbooks, protocols
+-- infrastructure/                 # Docker, Terraform (GCP), database migrations
+-- deploy/                         # Kubernetes manifests (dev/staging/prod)
+-- observability/                  # Prometheus + Grafana dashboards
```

---

## Graceful Degradation

ForgeOS runs with whatever is available:

| Component | Available | Unavailable |
|-----------|-----------|-------------|
| Anthropic/OpenAI API key | Real LLM calls | Simulated responses |
| PostgreSQL | Persistent storage + RLS | In-memory (lost on restart) |
| Redis | Distributed rate limiting | In-memory rate limiting |
| MCP servers | Real tool execution | "Not connected" errors |
| CrewAI/ADK/OpenClaw SDK | Native framework execution | Falls back to ForgeOS agentic loop |

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Architecture Overview](docs/architecture/overview.md) | OS layers, kernel, platform, adapters |
| [AgentOS Kernel](docs/architecture/kernel.md) | Admission, permissions, budgets, policies, capabilities |
| [Syscall Pipeline](docs/architecture/kernel.md) | 7-stage admission pipeline |
| [A2A Protocol](docs/architecture/a2a-protocol.md) | Agent-to-agent calling across frameworks |
| [A2H Protocol](docs/protocols/a2h-spec.md) | Agent-to-human interaction protocol |
| [Platform Layer](docs/architecture/platform-layer.md) | Registry, executor, scheduler, event bus, LLM router |
| [Stack Adapters](docs/architecture/stack-adapters.md) | ForgeOS, CrewAI, ADK, OpenClaw, Sandbox |
| [Python SDK](docs/guides/sdk.md) | Agent, Runtime, Kernel, Manifest, CLI |
| [Agent Manifest](docs/reference/agent-manifest.md) | Full `agent.yaml` schema |
| [API Reference](docs/reference/api-endpoints.md) | All FastAPI endpoints |
| [Creating Agents](docs/guides/creating-agents.md) | 5 lifecycles, 3 ownership types, tools |
| [Quick Start](docs/guides/quickstart.md) | Install, configure, boot, deploy |

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

Proprietary. All rights reserved.
