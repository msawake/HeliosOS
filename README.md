# ForgeOS

**Multi-Stack AI Agent Platform** -- Deploy, orchestrate, and manage AI agents across four framework adapters with five execution lifecycles, a real-time dashboard, and production infrastructure.

ForgeOS is the **framework** (the operating system). Agents are the **programs** that run inside it.

```
ForgeOS (the framework)
  Provides: registry, scheduling, event bus, LLM routing, tool execution,
            cost tracking, audit logging, dashboard, API, persistence

Agents (the workloads)
  Defined by: name, stack, execution type, tools, system prompt
  Deployed into ForgeOS via API, dashboard, or AI wizard
  Run using one of 4 stack adapters (ForgeOS, CrewAI, ADK, OpenClaw)
```

Think of ForgeOS the way you think of Kubernetes: the framework handles scheduling, networking, storage, and monitoring. Agents are the pods -- you define what they do, and the framework runs them.

---

## Architecture

```
                           +-----------------------+
                           |     Next.js Dashboard  |
                           |   (admin, chat, wizard) |
                           +-----------+-----------+
                                       |
                           +-----------v-----------+
                           |     FastAPI (61 endpoints) |
                           +-----------+-----------+
                                       |
          +----------------------------v----------------------------+
          |                   PLATFORM LAYER                        |
          |  +----------+ +----------+ +-----------+ +-----------+  |
          |  | Registry | | Executor | | Scheduler | | Event Bus |  |
          |  +----------+ +----------+ +-----------+ +-----------+  |
          |  +----------+ +----------+ +-----------+ +-----------+  |
          |  |LLM Router| |Agentic   | | Audit Log | | Metrics   |  |
          |  |(Anthropic | |Loop      | | + Alerts  | |(Prometheus|  |
          |  | + OpenAI) | |(tool-use)| |           | |  14 fams) |  |
          |  +----------+ +----------+ +-----------+ +-----------+  |
          +----------------------------+----------------------------+
                                       |
          +----------------------------v----------------------------+
          |                  STACK ADAPTERS                          |
          |  +----------+ +----------+ +-----------+ +-----------+  |
          |  | ForgeOS  | | CrewAI   | | Google    | | OpenClaw  |  |
          |  | (native) | | (crews)  | | ADK       | | (gateway) |  |
          |  +----------+ +----------+ +-----------+ +-----------+  |
          +----------------------------+----------------------------+
                                       |
          +----------------------------v----------------------------+
          |               CORE + COMPANIES                          |
          |  Database (Postgres/in-memory) | MCP Tool Executor      |
          |  Session Store | Hook Chain    | 5 Company Packages     |
          |  Cost Tracking | Knowledge Base | Workflow Engine        |
          +--------------------------------------------------------+
```

**Platform Layer** -- Framework services that all agents share (registry, executor, scheduler, event bus, LLM router, agentic tool-use loop, audit, metrics).

**Stack Adapters** -- Pluggable agent runtimes. Each implements the same `AgentStackAdapter` interface so the platform can manage them uniformly.

**Core + Companies** -- Database, MCP tools, hooks, session persistence, and 5 company packages (LeadForge, DealForge, TravelForge, InsureForge, HomeForge).

---

## Stack Adapters

| Stack | What It Does | SDK Required | Best For |
|-------|-------------|-------------|----------|
| **ForgeOS** | Native agentic loop via LLM Router | None | Default. Most flexible, full tool access |
| **CrewAI** | Role-based crews via CrewAI SDK | `crewai` | Multi-role collaboration patterns |
| **Google ADK** | Google Agent Development Kit | `google-adk` | Google ecosystem, Gemini models |
| **OpenClaw** | File-first agent via HTTP gateway | Node.js + openclaw2 | Markdown-driven, SOUL/HEARTBEAT pattern |

All four adapters fall back to the platform agentic loop when their SDK is not installed.

---

## Execution Types

Agents have one of five execution lifecycles:

| Type | Behavior | Example |
|------|----------|---------|
| `always_on` | Runs continuously in a loop | System health monitor (every 60s) |
| `scheduled` | Triggered by cron expression | Daily report generator (`0 9 * * *`) |
| `event_driven` | Triggered by event bus messages | Alert responder on `cost.exceeded` |
| `reflex` | On-demand, responds to API calls | Chat agent, single-turn Q&A |
| `autonomous` | Goal-directed, iterates until done | Research agent working toward an objective |

---

## Quick Start

```bash
# 1. Install
cd ~/Documents/one
pip install -e ".[dev]"

# 2. Configure
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

# 3. Boot the platform
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000

# 4. Start the dashboard (separate terminal)
cd dashboard && npm install && npm run dev

# 5. Deploy your first agent
curl -s -X POST http://localhost:5000/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello-agent",
    "stack": "forgeos",
    "execution_type": "reflex",
    "description": "A simple test agent",
    "chat_model": "claude-sonnet-4-5-20250514"
  }' | python3 -m json.tool
```

Then open http://localhost:3000 to see the dashboard.

---

## Project Structure

```
.
+-- src/                          # Platform + core framework (112 Python files, 30K lines)
|   +-- bootstrap.py              # Boot sequence: DB -> MCP -> tools -> adapters -> API
|   +-- platform/                 # Registry, executor, scheduler, event bus, LLM router
|   +-- core/                     # Database, hooks, session store, migrations
|   +-- mcp/                      # MCP server manager, tool executor, providers
|   +-- companies/                # 5 company packages (leadforge, dealforge, ...)
|   +-- dashboard/                # FastAPI app (61 endpoints)
|   +-- intelligence/             # Ontology, connectors, intelligence agents
|   +-- billing/                  # Stripe billing, usage enforcement, cost tracking
|   +-- api/                      # Auth (Firebase JWT + API keys), tenant management
|   +-- workflows/                # DAG workflow engine
|   +-- admin/                    # Admin monitoring tools
|
+-- stacks/                       # Stack adapter layer
|   +-- base.py                   # AgentStackAdapter ABC, AgentDefinition, enums
|   +-- forgeos/adapter.py        # Native ForgeOS adapter
|   +-- crewai/adapter.py         # CrewAI adapter
|   +-- adk/adapter.py            # Google ADK adapter
|   +-- openclaw/adapter.py       # OpenClaw gateway adapter
|
+-- dashboard/                    # Next.js 15 + React 19 + Tailwind frontend
|   +-- src/app/                  # Pages: agents, admin, clients, workflows, settings
|   +-- src/components/           # AppShell, Sidebar, Badge, StatCard
|   +-- src/lib/                  # API client, auth hooks, utilities
|
+-- agents/                       # Deployed agent configurations (74 agents)
|   +-- personal/                 # Per-user agents (21 agents across 3 users)
|   +-- shared/                   # Company-wide agents (53 agents)
|
+-- tests/                        # 730 tests across 42 files
+-- infrastructure/               # Docker, Terraform (GCP), database migrations
+-- deploy/                       # Kubernetes manifests (dev/staging/prod overlays)
+-- observability/                # Prometheus + Grafana dashboards
+-- resources/                    # MCP package catalog, agent templates, skill definitions
+-- docs/                         # Architecture, guides, reference, runbooks
```

---

## Key Concepts

### Framework vs Agents

| | **ForgeOS Framework** | **Agents** |
|---|---|---|
| **What** | The platform that runs agents | AI workloads that perform tasks |
| **Analogy** | Kubernetes | Pods |
| **Defined in** | `src/`, `stacks/` | `agents/`, API requests |
| **Provides** | Scheduling, routing, tools, persistence, monitoring | System prompt, tool selection, execution behavior |
| **Lifecycle** | Boots once, runs forever | Deployed, invoked, stopped, undeployed |
| **Config** | `.env`, company YAML | `AgentDefinition` (name, stack, type, tools) |

### Agent Definition

Every agent is defined by an `AgentDefinition` (see `stacks/base.py`):

```python
AgentDefinition(
    name="daily-report",
    stack="forgeos",                        # Which runtime to use
    execution_type=ExecutionType.SCHEDULED,  # When/how it runs
    ownership=OwnershipType.SHARED,          # Who can use it
    schedule="0 9 * * *",                   # Cron (for scheduled)
    tools=["mcp__google-workspace__*"],      # What tools it can use
    system_prompt="You generate daily reports...",
    llm_config=LLMConfig(chat_model="claude-sonnet-4-5-20250514"),
)
```

### How an Agent Runs

```
1. Deploy:  API request -> executor.deploy() -> registry + scaffold + adapter.create_agent()
2. Wire:    execution type determines lifecycle (loop, cron, event subscription, or on-demand)
3. Invoke:  executor.invoke() -> adapter.invoke() -> agentic loop (LLM <-> tools)
4. Tools:   LLM requests tool_use -> tool_executor routes to MCP/custom/platform tools
5. Result:  AgentResult with output text, tool calls, token count
```

---

## Graceful Degradation

ForgeOS runs with whatever is available:

| Component | Available | Unavailable |
|-----------|-----------|-------------|
| Anthropic API key | Real LLM calls | Simulated responses |
| PostgreSQL | Persistent storage + RLS | In-memory (lost on restart) |
| Redis | Distributed rate limiting | In-memory rate limiting |
| MCP servers | Real tool execution | "Not connected" errors |
| CrewAI/ADK SDK | Native framework execution | Falls back to ForgeOS agentic loop |

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Architecture Overview](docs/architecture/overview.md) | Framework vs agents, 3-layer design |
| [Platform Layer](docs/architecture/platform-layer.md) | Registry, executor, scheduler, event bus, LLM router |
| [Stack Adapters](docs/architecture/stack-adapters.md) | ForgeOS, CrewAI, ADK, OpenClaw comparison |
| [Quick Start](docs/guides/quickstart.md) | Install, configure, boot, deploy first agent |
| [Creating Agents](docs/guides/creating-agents.md) | 5 execution types, 3 ownership types, tools |
| [Agent Tools & MCP](docs/guides/agent-tools.md) | MCP servers, custom tools, tool executor |
| [API Reference](docs/reference/api-endpoints.md) | All 61 FastAPI endpoints |
| [Configuration](docs/reference/configuration.md) | Environment variables, YAML config, LLM config |
| [Deployment](docs/operations/deployment.md) | Docker, Kubernetes, GCP |
| [Monitoring](docs/operations/monitoring.md) | Prometheus metrics, Grafana, alerts |
| [Runbooks](docs/runbooks/) | Incident response, DB recovery |
| [Project History](docs/development/project-history.md) | Evolution from v1 to v3 |

---

## Tech Stack

- **Backend:** Python 3.11+, FastAPI, asyncio, psycopg3
- **Frontend:** Next.js 15, React 19, Tailwind CSS, TypeScript
- **LLM Providers:** Anthropic (Claude), OpenAI (GPT/o3)
- **Database:** PostgreSQL 16 with pgvector, Row-Level Security
- **Tools:** Model Context Protocol (MCP) servers
- **Infrastructure:** Docker, Kubernetes, Terraform (GCP), GitHub Actions
- **Monitoring:** Prometheus, Grafana, structured logging

---

## Running Tests

```bash
PYTHONPATH=. python3 -m pytest                          # All 730 tests
PYTHONPATH=. python3 -m pytest tests/test_platform_executor.py  # Single file
PYTHONPATH=. python3 -m pytest -k "test_deploy"         # By name pattern
```

## License

Proprietary. All rights reserved.
