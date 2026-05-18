# ForgeOS Capabilities Reference

Complete inventory of platform capabilities: MCP servers, tools, protocols, APIs, and platform features.

---

## MCP Servers

### Configured Servers (11)

**Tier 1 — Required**

| Name | Package | Purpose |
|------|---------|---------|
| `github` | `@modelcontextprotocol/server-github` | Repository operations, PRs, code reviews |
| `google-workspace` | `google-workspace-mcp` | Docs, Sheets, Gmail, Calendar |
| `slack` | `@anthropic/mcp-server-slack` | Channel/thread management |
| `postgres` | `@modelcontextprotocol/server-postgres` | SQL database access |
| `stripe` | `@stripe/mcp` | Billing and payment operations |

**Tier 2 — Optional**

| Name | Package | Purpose |
|------|---------|---------|
| `crm` | `crm-mcp` | CRM integration |
| `google-ads` | `google-ads-mcp` | Ad campaign management |
| `pinecone` | `pinecone-mcp` | Vector database / embeddings |
| `linkedin` | `linkedin-mcp` | LinkedIn operations |

**Tier 3 — Optional**

| Name | Package | Purpose |
|------|---------|---------|
| `datadog` | `datadog-mcp` | Observability and monitoring |
| `aws` | `aws-mcp` | AWS service operations |

### MCP Registry

- **4,500+ packages** indexed from `resources/mcps/packages-list.json`
- Categories: databases, communication, web scraping, productivity, financial, gaming, and more
- Managed via `src/platform/mcp_registry.py`
- Lifecycle (connect / tool discovery / disconnect) via `src/mcp/server_manager.py`
- Transport: stdio (npx or uvx)
- Graceful degradation when MCP SDK is not installed

---

## Agent Tools

### Platform Tools (21) — `src/mcp/platform_tools.py`

**CRM**
- `platform__crm_search_leads` — Search by query, status, score
- `platform__crm_update_lead` — Update status, score, owner, deal value
- `platform__crm_get_pipeline` — Pipeline summary with conversion rates
- `platform__crm_create_activity` — Log calls, emails, meetings, notes

**HTTP**
- `platform__http_fetch` — Web scraping with selectors
- `platform__http_post` — External API calls

**Advertising**
- `platform__ads_get_campaigns` — Campaign metrics (Google Ads, Meta, LinkedIn, Twitter)
- `platform__ads_update_bid` — Update budgets, bid strategies, targets

**Real Estate / MLS**
- `platform__mls_search_listings` — Search by location, price, bedrooms
- `platform__mls_get_listing` — Full property details

**Insurance**
- `platform__insurance_get_quotes` — Multi-carrier quotes (auto, home, life)
- `platform__insurance_compare_rates` — Side-by-side comparison with discounts

**GitHub**
- `platform__github_get_pr` — Pull request details and CI status
- `platform__github_create_review` — Code review with inline comments

**Inter-Agent Messaging**
- `platform__send_message` — Async agent-to-agent message dispatch
- `platform__read_messages` — Mailbox with unread filtering

**File System**
- `platform__file_read` — Read files (workspace + source files)
- `platform__file_write` — Write files (workspace only)
- `platform__file_list` — Directory listing

**Custom**
- `platform__custom_tool` — Pluggable company-specific handler

### Admin Tools (12) — `src/admin/tools.py`

- `admin_system_health` — Agent / approval / workflow / cost snapshot
- `admin_list_agents` — Filter by department, tier, status
- `admin_invoke_agent` — Manual agent triggering
- `admin_stop_agent` — Halt a running agent
- `admin_approve_reject` — HITL request decisions
- `admin_list_approvals` — Pending approvals with SLA tracking
- `admin_workflow_status` — Progress reporting
- `admin_workflow_control` — Pause / resume / cancel / retry
- `admin_query_metrics` — System and business metrics
- `admin_query_events` — Event bus search
- `admin_search_knowledge` — Knowledge base queries
- `admin_add_knowledge` — Record decisions and incidents

### Ontology / Intelligence Tools (5) — `src/intelligence/tools.py`

- `ontology_query_objects` — Search business objects by type and properties
- `ontology_get_neighbors` — Traverse entity relationships
- `ontology_aggregate` — Count, sum, avg grouped by type
- `ontology_search` — Full-text search across the ontology
- `ontology_get_schema` — Schema discovery

---

## Agent-to-Agent Protocol (A2A) — `src/platform/a2a.py`

Four `agent__*` tools injected at invocation:

| Tool | Signature | Description |
|------|-----------|-------------|
| `agent__call` | `(namespace, name, task, context, timeout)` | Synchronous invocation |
| `agent__async_call` | `(namespace, name, task, context)` | Returns `job_id` |
| `agent__await` | `(job_id, timeout)` | Wait for async result |
| `agent__list_available` | `(namespace, department, label)` | Discover callable peers |

**Guarantees:**
- Addressed by `(namespace, agent_name)`
- ACL-checked via callee's `spec.capabilities.a2a.canBeCalledBy`
- Cycle detection + max depth enforcement (default: 5)
- Delegation context propagates remaining budget (tokens, USD)
- Framework-agnostic — works across all five stack adapters

---

## Agent-to-Human Protocol (A2H) — `src/platform/a2h.py`

Four `human__*` tools:

| Tool | Description |
|------|-------------|
| `human__ask(question, response_type, options, priority, deadline)` | Blocking HITL request |
| `human__notify(message, priority, channel)` | One-way notification |
| `human__check(request_id)` | Status polling |
| `human__list_available(state_filter)` | Human availability |

**Request types:** QUESTION, APPROVAL, NOTIFICATION, TASK

**Response types:** CHOICE, APPROVAL, TEXT, NUMBER, CONFIRM, FORM, NONE

**Priority levels:** `critical`, `high`, `medium`, `low`

---

## Stack Adapters (5) — `stacks/`

| Adapter | File | Runtime |
|---------|------|---------|
| ForgeOS (native) | `stacks/forgeos/adapter.py` | Native agentic loop |
| CrewAI | `stacks/crewai/adapter.py` | `Crew.kickoff()` |
| Google ADK | `stacks/adk/adapter.py` | ADK Runner |
| OpenClaw | `stacks/openclaw/adapter.py` | HTTP gateway subprocess |
| Sandbox | `stacks/sandbox/adapter.py` | Docker / Kubernetes container |

Each implements: `create_agent()`, `invoke()`, `start_loop()`, `stop()`, `scaffold_files()`.  
All fall back to the platform agentic loop when the native runtime is unavailable.

---

## Execution Lifecycle Types (6)

| Type | Description |
|------|-------------|
| `always_on` | Persistent standing agent |
| `scheduled` | Cron-triggered |
| `event_driven` | Reacts to pub/sub events |
| `reflex` | Real-time agentic loop |
| `sprint` | Project-duration team (up to 8 members) |
| `burst` | Dynamic per-task group |

---

## LLM Providers

| Provider | Default Models |
|----------|---------------|
| Anthropic | `claude-opus-4-6` (orchestrator), `claude-sonnet-4-5-20250514` (doer), `claude-haiku-4-5-20251001` (classifier) |
| OpenAI | `gpt-4o`, `gpt-4o-mini` |
| Google | `gemini-2.0-flash` |

**Router features** (`src/platform/llm_router.py`):
- Automatic provider selection from model prefix (`claude-*` → Anthropic, `gpt-*`/`o3-*` → OpenAI)
- Separate chat vs. reasoning model routing
- Exponential backoff retry (3 attempts)
- Fallback provider with audit event on switch

---

## Platform Kernel — `src/platform/kernel.py`

Seven subsystems enforced on every meaningful action:

| Subsystem | Responsibility |
|-----------|---------------|
| `AdmissionController` | Contract validation at deploy time |
| `PermissionManager` | Tool and A2A ACL checks |
| `BudgetManager` | Daily token and per-session USD limits |
| `PolicyEngine` | Declarative rule evaluation (Rego + JSON) |
| `DataBoundaryManager` | Namespace isolation + PII masking |
| `CapabilityManager` | Opaque runtime grants with expiry + revocation |
| `AuditRecorder` | Immutable, hash-chained decision trail |

### Syscall Pipeline (opt-in: `FORGEOS_SYSCALL_PIPELINE=1`)

7-stage enforcement chain:

```
identity → capability → quota/budget → policy → boundary → dispatch → audit
```

Legacy path (`src/core/hooks.py`, 7 checks) runs by default. Both paths are safe; the env var selects which executes.

---

## Process Model — `src/platform/process.py`

- **Stable PID** per agent process
- **Phase machine:** `Pending → Running → Succeeded / Failed / Quarantined`
- **Resource accounting:** tokens, USD, tool calls, wall-clock time
- **Checkpoint / restore:** `src/platform/checkpoint.py` — preemption and durable resume

---

## Event System — `src/platform/event_bus.py`

- Pub/sub dispatch across agents and departments
- Durable event store (`durable_event_store.py`) for async A2A jobs
- Inter-agent mailbox with unread filtering
- History ring buffer (max 1,000 events)
- API: `POST /api/platform/events`, `GET /api/events`

---

## Workflow Engine

- DAG-based task orchestration
- Pause / resume / cancel / retry
- Status tracking per step
- API: `GET /api/workflows`, `GET /api/workflows/{id}`

---

## Scheduler — `src/platform/scheduler.py`

- Cron-based triggers for `scheduled` agent types
- Job persistence across restarts
- Integrates with `src/platform/triggers.py` (`TriggerSource` protocol)

---

## Governance & Policy

**Budgets (company defaults)**

| Scope | Limit |
|-------|-------|
| Daily tokens | 10,000,000 |
| Critical reserve | 10% |
| Per-session USD | $50 |
| Monthly infrastructure | $5,000 |

**Department daily token allocations**

| Department | Tokens |
|-----------|--------|
| Sales | 3,000,000 |
| Marketing | 2,500,000 |
| Operations | 600,000 |
| Legal | 300,000 |
| Finance | 500,000 |
| HR | 100,000 |

**Rate limits**

- Max tool calls per session: 100
- Max API calls per minute: 30
- Max concurrent agents per type: 10

**Circuit breakers**

- Task failure threshold: 3 failures
- System failure rate: 20% over 60 minutes
- External service errors: 5 over 10 minutes

**Audit sampling (HITL)**
- Outreach emails: 10%
- Ad spend changes: 15%
- Reports: 100%

---

## REST API — `src/dashboard/fastapi_app.py`

### System

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | System health snapshot |
| GET | `/api/readiness` | Kubernetes readiness probe |
| GET | `/api/liveness` | Liveness check |
| GET | `/metrics` | Prometheus metrics |

### Agent Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/platform/overview` | Registry summary |
| GET | `/api/platform/agents` | List agents (filters: stack, execution_type, ownership, department) |
| GET | `/api/platform/agents/{id}` | Agent details |
| POST | `/api/platform/agents` | Deploy agent |
| PUT | `/api/platform/agents/{id}` | Update agent |
| POST | `/api/platform/agents/{id}/invoke` | Invoke with custom prompt |
| POST | `/api/platform/agents/{id}/stop` | Halt agent |
| DELETE | `/api/platform/agents/{id}` | Undeploy |

### Chat

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/platform/agents/{id}/chat/stream` | Streaming SSE chat |
| GET | `/api/platform/agents/{id}/chat/sessions` | List sessions |
| GET | `/api/platform/agents/{id}/chat/history` | Chat history |
| DELETE | `/api/platform/agents/{id}/chat/sessions/{sid}` | Clear session |

### Approvals (HITL)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/approvals` | Pending approvals |
| GET | `/api/approvals/{id}` | Approval details |
| POST | `/api/approvals/{id}/approve` | Approve with reason |
| POST | `/api/approvals/{id}/reject` | Reject with reason |

### Workflows & Events

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/workflows` | List workflows |
| GET | `/api/workflows/{id}` | Workflow status |
| GET | `/api/events` | Query events |
| POST | `/api/platform/events` | Fire event |

### Admin & Intelligence

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/admin/chat` | Admin orchestrator chat |
| POST | `/api/admin/chat/stream` | Streaming admin chat |
| GET | `/api/admin/health` | Admin system health |
| GET | `/api/admin/metrics` | Metrics dashboard |
| GET | `/api/admin/events` | Event search |
| GET | `/api/admin/knowledge` | Knowledge search |
| POST | `/api/intelligence/ask` | Ontology query |

### Auth & Wizard

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/token` | Get API token |
| GET | `/api/me` | Current user info |
| POST | `/api/platform/wizard/chat` | Interactive agent builder |

### Docs

| Path | Description |
|------|-------------|
| `/docs` | Swagger UI |
| `/redoc` | ReDoc |
| `/openapi.json` | OpenAPI schema |

---

## CLI — `src/forgeos_sdk/cli.py`

```bash
forgeos deploy agent.yaml          # Validate + POST to /api/platform/agents
forgeos list                       # List all agents
forgeos invoke <id> "prompt"       # Invoke agent with prompt
forgeos get <id>                   # Agent details
forgeos undeploy <id>              # Remove agent
forgeos validate agent.yaml        # Validate manifest only
forgeos health                     # System health check
```

---

## SDK Runtime API — `src/forgeos_sdk/`

```python
from forgeos_sdk import runtime

# Budget & permissions
remaining = await runtime.get_budget()
allowed = await runtime.check_tool("mcp__gmail__send")

# Agent-to-agent
result = await runtime.call_agent("sales-team", "cfo", task="Q4 analysis")

# Agent-to-human
answer = await runtime.ask_human(
    question="Approve the $2.5M deal?",
    response_type="choice",
    options=["Approve", "Reject"],
    priority="high",
)

# Process checkpointing
await runtime.save_checkpoint({"progress": "step_3"})
```

---

## Company Packages (6) — `src/companies/`

Each ships as a fixture workload with `agent_configs.py`, `workflows.py`, `knowledge.py`, `config.yaml`, `demo.py`.

| Package | Domain |
|---------|--------|
| **LeadForge AI** | B2B sales — BANT scoring, outreach, SDR workflows |
| **DealForge AI** | M&A — deal tracking, due diligence |
| **TravelForge AI** | Travel — itinerary, booking, customer service |
| **InsureForge AI** | Insurance — multi-carrier quoting, claims |
| **HomeForge AI** | Real estate — MLS search, buyer/seller flows |
| **Practical** | General-purpose reference implementation |

---

## Observability

- **Prometheus metrics** — 14 metric families (agent lifecycle, invocations, costs, tool calls) via `src/platform/metrics.py`
- **Audit trail** — Immutable hash-chained log via `src/platform/audit.py`
- **Alerts** — Multi-destination dispatch (Slack, PagerDuty, log) via `src/platform/alerts.py`
- **Prometheus endpoint** — `GET /metrics`

---

## Infrastructure

| Layer | Technology |
|-------|-----------|
| Containerization | Docker (`infrastructure/docker/`) |
| Orchestration | Kubernetes + Kustomize overlays: dev / staging / prod (`deploy/k8s/`) |
| Autoscaling | HPA (`hpa-api.yaml`) + Pod Disruption Budget |
| Cloud | GCP — Cloud SQL, Redis, Cloud Run, VPC, Secret Manager (`infrastructure/terraform/gcp/`) |
| Database | PostgreSQL with multi-tenant RLS + 5 migrations |
| CI/CD | GitHub Actions: test → build → push to GHCR |

---

## Capability Summary

| Category | Count |
|----------|-------|
| MCP servers (configured) | 11 |
| MCP packages (registry) | 4,500+ |
| Platform tools | 21 |
| Admin tools | 12 |
| Ontology tools | 5 |
| A2A tools | 4 |
| A2H tools | 4 |
| Stack adapters | 5 |
| Execution lifecycle types | 6 |
| API endpoints | 35+ |
| LLM providers | 3 (Anthropic, OpenAI, Google) |
| Kernel subsystems | 7 |
| Company packages | 6 |
