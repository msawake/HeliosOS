# ForgeOS Architecture

## Overview
ForgeOS is an "AI Operating System" framework designed to orchestrate, govern, and scale AI agents across diverse frameworks and business domains. It follows an OS metaphor where the **Framework** is the kernel/infrastructure and **Agents** are the workloads.

Five company packages ship as fixtures: **LeadForge AI** (B2B sales), **DealForge AI** (M&A), **TravelForge AI** (travel), **InsureForge AI** (insurance), **HomeForge AI** (real estate). They are example workloads, not the framework itself.

---

## Architectural Layers

ForgeOS is structured into three primary layers:

1. **Stack Adapters (`stacks/`)** — Runtime layer. Unified interface (`AgentStackAdapter`) for CrewAI, ADK, OpenClaw, Sandbox, and ForgeOS native frameworks.
2. **Platform Layer (`src/platform/`)** — Orchestration layer. Agent lifecycles, registries, scheduling, A2A/A2H protocols, LLM routing, governance kernel.
3. **Core & Companies (`src/core/`, `src/companies/`)** — Infrastructure and business logic. DB drivers, legacy hooks, and vertical-specific agent packs.

---

## Current Folder Structure

```
forgeos-gh/
│
├── src/                              # BACKEND — Python source
│   │
│   ├── platform/                     # ── Orchestration (workers / long-lived processes)
│   │   ├── kernel.py                 #    Policy enforcement, admission facade
│   │   ├── syscall.py                #    Unified admission pipeline (opt-in FORGEOS_SYSCALL_PIPELINE=1)
│   │   ├── executor.py               #    Deploy, invoke, recover agents (central dispatcher)
│   │   ├── registry.py               #    Universal agent registry
│   │   ├── scheduler.py              #    Cron-based job scheduling
│   │   ├── event_bus.py              #    Pub/sub for event-driven agents
│   │   ├── agentic_loop.py           #    LLM → tool_use → result loop (sync + streaming)
│   │   ├── llm_router.py             #    Anthropic/OpenAI routing, failover, streaming
│   │   ├── a2a.py + a2a_contracts.py #    Agent-to-Agent protocol (ACL, cycle detection)
│   │   ├── a2h.py + h2a.py           #    Agent↔Human protocol (approval gating)
│   │   ├── process.py                #    AgentProcess: PID, phase machine, resource accounting
│   │   ├── checkpoint.py             #    Preemption + durable resume
│   │   ├── capabilities.py           #    Opaque capability tokens (grants with expiry)
│   │   ├── persistence.py            #    Generic store abstraction
│   │   ├── client_store.py           #    Per-client config store
│   │   ├── audit.py                  #    Hash-chained audit trail
│   │   ├── alerts.py                 #    Multi-destination alerts (Slack, PagerDuty, log)
│   │   ├── metrics.py                #    Prometheus metrics (14 families)
│   │   ├── skill_registry.py         #    Registered skills catalogue
│   │   ├── mcp_registry.py           #    Platform-level MCP binding index
│   │   ├── package_registry.py       #    Versioned agent/tool packages
│   │   ├── durable_event_store.py    #    Durable event log (event bus + A2A async jobs)
│   │   ├── environment.py            #    Sandbox environment management
│   │   ├── agent_definitions.py      #    Built-in agent definition library (141 KB)
│   │   ├── agent_wizard_planner.py   #    Agent creation wizard
│   │   ├── wizard_agent.py           #    Wizard agent runner
│   │   └── triggers.py               #    Trigger definitions for scheduler/event bus
│   │
│   ├── core/                         # ── Infrastructure drivers (legacy)
│   │   ├── database.py               #    Multi-tenant PostgreSQL + RLS + connection pool
│   │   ├── session_store.py          #    In-memory or PostgreSQL session persistence
│   │   ├── model_client.py           #    LLMClient protocol + Anthropic/OpenAI impls
│   │   ├── hooks.py                  #    Legacy 7-check governance chain (default path)
│   │   ├── agent_invoker.py          #    Legacy 3-tier agent hierarchy orchestration
│   │   ├── claude_client.py          #    Pre-platform agentic loop (being phased out)
│   │   ├── redis_rate_limiter.py     #    Redis-backed rate limiting
│   │   ├── secrets.py                #    Secret management + lease auditing
│   │   ├── migrations.py             #    DB migration runner
│   │   └── telemetry.py              #    Basic telemetry helpers
│   │
│   ├── mcp/                          # ── MCP (Model Context Protocol) layer
│   │   ├── tool_executor.py          #    Routes mcp__* → MCP servers, company__* → in-process
│   │   ├── server_manager.py         #    MCP server lifecycle (connect, discover, disconnect)
│   │   ├── client_mcp_manager.py     #    Per-client MCP connections with LRU eviction
│   │   ├── platform_tools.py         #    Platform-level built-in tools (54 KB)
│   │   ├── custom_tools.py           #    Custom tool definitions (29 KB)
│   │   ├── persistence.py            #    MCP config persistence
│   │   ├── pubsub_bus.py             #    MCP pub/sub integration
│   │   └── providers/                #    External MCP providers
│   │       ├── crm_provider.py       #      CRM integration
│   │       ├── github_provider.py    #      GitHub integration
│   │       ├── http_provider.py      #      Generic HTTP tools
│   │       └── messaging_provider.py #      Slack/email messaging
│   │
│   ├── forgeos_sdk/                  # ── Public Python SDK (distributed separately)
│   │   ├── manifest.py               #    Pydantic schema for agent.yaml (forgeos/v1 + agentos/v1)
│   │   ├── agent.py                  #    Agent class (declarative) + AgentBuilder (fluent)
│   │   ├── client.py                 #    ForgeOSClient sync HTTP wrapper
│   │   ├── kernel.py                 #    Kernel accessor (in-process or remote)
│   │   ├── cli.py                    #    forgeos deploy/list/invoke/undeploy/health CLI
│   │   └── runtime.py                #    SDK runtime (singleton, in-process)
│   │
│   ├── forgeos_sandbox/              # ── Sandbox runtime helper
│   │   ├── runner.py                 #    In-process sandbox runner
│   │   └── env_manager.py            #    Environment lifecycle management
│   │
│   ├── intelligence/                 # ── Market intelligence / data connectors
│   │   ├── agents.py                 #    Intelligence agent definitions
│   │   ├── ontology.py               #    Domain ontology (15 KB)
│   │   ├── sync_engine.py            #    Data sync orchestration
│   │   ├── tools.py                  #    Intelligence tools
│   │   ├── connectors/               #    CRM, data source connectors
│   │   └── schemas/                  #    Data schemas
│   │
│   ├── companies/                    # ── Business verticals (workload packs)
│   │   ├── leadforge/                #    B2B sales agents + workflows
│   │   ├── dealforge/                #    M&A analysis agents
│   │   ├── travelforge/              #    Travel booking agents
│   │   ├── insureforge/              #    Insurance processing agents
│   │   ├── homeforge/                #    Real estate agents
│   │   └── practical/                #    Generic practical examples
│   │       # Each vertical: agent_configs.py, workflows.py, knowledge.py, config.yaml, demo.py
│   │
│   ├── admin/                        # ── Administration
│   │   ├── auth.py                   #    JWT/session authentication
│   │   └── tenants.py                #    Tenant management
│   │
│   ├── api/                          # ── Async API / cloud tasks
│   │   ├── cloud_tasks.py            #    GCP Cloud Tasks integration
│   │   └── definitions.py            #    API type definitions
│   │
│   ├── billing/                      # ── Billing
│   │   ├── stripe_billing.py         #    Stripe integration
│   │   └── plans.py                  #    Plan/subscription definitions
│   │
│   ├── workflows/                    # ── Workflow engine
│   │   ├── definitions.py            #    Workflow step definitions
│   │   └── cloud_tasks.py            #    Cloud-backed workflow execution
│   │
│   ├── config/                       # ── Configuration
│   │   └── agent_configs.py          #    Global agent config defaults
│   │
│   └── dashboard/                    # ── API servers + legacy frontend
│       ├── fastapi_app.py            #    FastAPI REST API (~70 endpoints, 117 KB) [BACKEND]
│       ├── app.py                    #    Flask alternative entry point (legacy) [BACKEND]
│       └── frontend/                 #    Vite-based legacy frontend [FRONTEND]
│           └── src/pages/
│
├── stacks/                           # ADAPTERS — Framework runtime adapters
│   ├── base.py                       #    AgentStackAdapter ABC
│   ├── forgeos/adapter.py            #    ForgeOS native agentic loop
│   ├── crewai/adapter.py             #    CrewAI SDK (Crew.kickoff + fallback)
│   ├── adk/adapter.py                #    Google ADK Runner + fallback
│   ├── openclaw/adapter.py           #    HTTP gateway subprocess + fallback
│   └── sandbox/
│       ├── adapter.py                #    Docker container sandbox
│       └── k8s_adapter.py            #    Kubernetes pod sandbox
│
├── dashboard/                        # FRONTEND — Next.js 15 (main UI)
│   └── src/
│       ├── app/                      #    Next.js App Router pages
│       │   ├── agents/               #      Agent list, [id] detail, create
│       │   ├── workflows/            #      Workflow list, [id] detail
│       │   ├── environments/         #      Sandbox environments, [id] detail
│       │   ├── clients/              #      Client list, [id] detail
│       │   ├── approvals/            #      Human-in-the-loop approvals
│       │   ├── intelligence/         #      Market intelligence dashboard
│       │   ├── settings/             #      Platform settings
│       │   ├── login/                #      Auth pages
│       │   └── admin/                #      Admin panels
│       │       ├── audit/            #        Audit log viewer
│       │       ├── chat/             #        Admin chat interface
│       │       ├── events/           #        Event bus viewer
│       │       ├── jobs/             #        Scheduled jobs
│       │       ├── knowledge/        #        Knowledge base management
│       │       ├── mcps/             #        MCP server management
│       │       └── skills/           #        Skills catalogue
│       ├── components/               #    Shared React components
│       └── lib/
│           └── hooks/                #    Custom React hooks
│
├── a2h/                              # PROTOCOL PACKAGE — Agent-to-Human (separate pip pkg)
│   └── a2h/
│       ├── gateway.py                #    A2H protocol gateway
│       ├── channels.py               #    Communication channels
│       ├── models.py                 #    Protocol models
│       ├── server.py                 #    A2H server
│       └── store.py                  #    Approval store
│
├── tests/                            # TESTS — Integration test suite (65 files, flat)
│   └── load/                         #    k6 load/performance tests
│
├── examples/                         # EXAMPLES — Functional smoke tests
│   ├── companies/tests/              #    Per-vertical integration tests
│   ├── a2a/ crewai/ adk/ forgeos/   #    Framework usage examples
│   └── platform/ advanced/           #    Platform usage examples
│
├── infrastructure/                   # INFRA — Deployment configuration
│   ├── database/                     #    7 SQL migrations (001-007)
│   ├── docker/                       #    Dockerfile, Dockerfile.sandbox, docker-compose
│   ├── terraform/gcp/                #    Cloud SQL, Redis, Cloud Run, VPC, Secret Manager
│   └── scripts/                      #    Infra helper scripts
│
├── deploy/                           # KUBERNETES — K8s manifests
│   └── k8s/
│       ├── base/                     #    Base manifests (deployment, service, ingress, HPA)
│       │   └── observability/        #      Grafana/Prometheus K8s configs
│       ├── overlays/                 #    Kustomize overlays (dev/staging/prod/gke-dev)
│       └── chaos/                    #    Chaos engineering manifests
│
├── observability/                    # OBSERVABILITY — Grafana dashboards
│
├── resources/                        # RESOURCES — Static agent/MCP/skill definitions
│   ├── agents/                       #    Agent YAML manifests by department
│   │   └── (executive/sales/marketing/finance/hr/legal/operations)
│   ├── mcps/packages/                #    MCP server package catalogue (~25 categories)
│   └── skills/                       #    Agent skill packs (engineering, QA, etc.)
│
├── agents/                           # RUNTIME — gitignored, personal/shared agent configs
├── files/knowledge/                  # KNOWLEDGE — Knowledge base files
├── tools/                            # TOOLS — forgeos-mcp-server.py (MCP server entry)
├── config/                           # CONFIG — Runtime config files
└── docs/                             # DOCS — Architecture, guides, protocols, runbooks
```

---

## Component Classification

| Component | Type | Technology |
|---|---|---|
| `src/platform/` | Backend workers + service layer | Python, asyncio |
| `src/dashboard/fastapi_app.py` | Backend API | FastAPI, Python |
| `src/dashboard/app.py` | Backend API (legacy) | Flask, Python |
| `stacks/` | Backend adapters | Python |
| `src/core/` | Backend infrastructure | Python, PostgreSQL, Redis |
| `src/mcp/` | Backend tool gateway | Python, MCP protocol |
| `src/forgeos_sdk/` | SDK / CLI package | Python |
| `src/companies/` | Backend business logic | Python |
| `src/intelligence/` | Backend data pipeline | Python |
| `src/admin/` | Backend auth/admin | Python |
| `src/billing/` | Backend billing | Python, Stripe |
| `src/workflows/` | Backend workflow engine | Python |
| `src/forgeos_sandbox/` | Backend sandbox runner | Python, Docker |
| `dashboard/` | Frontend (main) | Next.js 15, React 19, Tailwind |
| `src/dashboard/frontend/` | Frontend (legacy) | Vite, React |
| `a2h/` | Protocol microservice | Python, asyncio |
| `stacks/sandbox/k8s_adapter.py` | Container orchestration | Python, Kubernetes |
| `infrastructure/` | Infrastructure-as-Code | Docker, Terraform, K8s |
| `observability/` | Observability | Grafana, Prometheus |

---

## Data Flow & Connections

```
                          ┌─────────────────────────────┐
                          │  Next.js Dashboard (port 3000)│
                          └──────────────┬──────────────┘
                                         │ REST / SSE
                          ┌──────────────▼──────────────┐
                          │  FastAPI Backend (port 5000)  │
                          └──────────────┬──────────────┘
                                         │
              ┌──────────────────────────▼────────────────────────────┐
              │                   Platform Layer                        │
              │  PlatformExecutor → AgentRegistry → Stack Adapters     │
              │  Kernel (Syscall Pipeline) → ToolExecutor              │
              │  LLMRouter → Anthropic/OpenAI APIs                     │
              │  EventBus ↔ SchedulerEngine ↔ A2A Protocol            │
              └─────────┬─────────────────────────┬───────────────────┘
                        │                         │
          ┌─────────────▼──────────┐   ┌─────────▼─────────────┐
          │  MCP Tool Gateway       │   │  A2H Protocol Gateway  │
          │  (MCP servers,          │   │  (human approval,      │
          │   internal handlers)    │   │   HITL checkpoints)    │
          └────────────────────────┘   └───────────────────────┘
```

---

## Tests

### Test Coverage

The project has a substantial test suite (~1,132 tests across 63 files). Tests are organized across three locations:

#### `tests/` — 65 integration test files (flat structure)
Primary test suite. Covers:

| Category | Files |
|---|---|
| Platform orchestration | `test_platform_executor.py`, `test_platform_registry.py`, `test_platform_scheduler.py`, `test_platform_event_bus.py`, `test_platform_process.py`, `test_platform_checkpoint.py`, `test_platform_generic.py`, `test_platform_base.py`, `test_platform_adapters.py` |
| Kernel & governance | `test_kernel.py`, `test_kernel_tool_gate.py`, `test_platform_syscall.py`, `test_hooks.py`, `test_admission_registers_contracts.py`, `test_platform_capabilities.py`, `test_platform_budget_reservation.py`, `test_crewai_adk_kernel_gate.py` |
| A2A / A2H / H2A protocols | `test_a2a.py`, `test_a2a_capability_and_contract.py`, `test_platform_a2a_contracts.py`, `test_a2h_protocol.py`, `test_h2a_protocol.py` |
| LLM & model routing | `test_llm_router_failover.py`, `test_llm_router_streaming.py`, `test_model_client.py` |
| MCP tools | `test_mcp_tools.py`, `test_mcp_manager.py`, `test_tool_executor_syscall_adoption.py`, `test_tool_retries.py`, `test_openclaw_tool_proxy.py`, `test_crewai_tool_binding.py` |
| Business verticals | `test_dealforge.py`, `test_homeforge.py`, `test_insureforge.py`, `test_travelforge.py` |
| Observability | `test_audit_log.py`, `test_audit_hash_chain.py`, `test_metrics.py`, `test_alerts.py`, `test_cost_tracking.py` |
| Infra / data | `test_migrations.py`, `test_rls.py`, `test_session_and_redis.py`, `test_client_store.py`, `test_platform_durable_event_store.py`, `test_platform_package_registry.py` |
| SDK / CLI | `test_sdk_runtime.py`, `test_manifest_canonical.py`, `test_platform_providers.py` |
| Resilience | `test_chaos_resilience.py`, `test_triggers_and_preemption.py` |
| Other | `test_hitl_system.py`, `test_saas_platform.py`, `test_connectors.py`, `test_intelligence_agents.py`, `test_agent_wizard_planner.py`, `test_adk_integration.py`, `test_secrets_audit_and_leases.py`, `test_admin_tools.py`, `test_ontology.py`, `test_practical.py`, `test_examples.py`, `test_all_examples.py`, `test_cloud_services.py` |

#### `tests/load/` — k6 Performance tests
- `smoke.js` — Minimal load verification
- `steady.js` — Steady-state sustained load
- `spike.js` — Spike load simulation
- `invoke-agent.js` — Agent invocation load test

#### `a2h/tests/` — Protocol conformance tests
- `test_protocol.py` — A2H protocol contract tests

#### `examples/companies/tests/` — Per-vertical functional tests
- `test_agent_configs.py`, `test_workflows.py`
- `test_dealforge.py`, `test_homeforge.py`, `test_insureforge.py`, `test_travelforge.py`, `test_practical.py`

### Running Tests

```bash
# All tests
PYTHONPATH=. python3 -m pytest

# Single file
PYTHONPATH=. python3 -m pytest tests/test_platform_executor.py

# Pattern filter
PYTHONPATH=. python3 -m pytest -k "test_kernel"

# A2H conformance
PYTHONPATH=. python3 -m pytest a2h/tests/

# Company vertical tests
PYTHONPATH=. python3 -m pytest examples/companies/tests/
```

---

## Proposed Refactor: Screaming Architecture + Hexagonal + DDD + Vertical Slicing

### Guiding Principles

- **Screaming Architecture** — folder names announce the business capability, not the framework. Reading `forgeos/agent_execution/` tells you what the system does; reading `forgeos/utils/` tells you nothing.
- **Hexagonal (Ports & Adapters)** — every domain is shielded from infrastructure by ports (interfaces) and adapters (implementations). The domain has zero import of FastAPI, SQLAlchemy, Redis, Stripe, or any external SDK.
- **DDD Aggregates** — each vertical slice owns its aggregate root, value objects, and domain events. No cross-slice direct imports — communication goes through ports or events.
- **Vertical Slicing** — a feature ticket touches exactly one vertical folder from API to domain to adapter. No horizontal "service" or "util" sprawl.

### Proposed Structure

```
forgeos/
│
├── kernel/                           # SCREAMS: I am the OS kernel — govern every action
│   ├── domain/
│   │   ├── admission.py              #   Aggregate: AdmissionDecision, AdmissionContext
│   │   ├── capability.py             #   Value object: CapabilityToken (expiry, revocation)
│   │   ├── policy.py                 #   Policy rules, PII rules, boundary rules
│   │   ├── budget.py                 #   Budget aggregate: daily_usd, per_task_usd, reservations
│   │   └── events.py                 #   Domain events: CapabilityGranted, BudgetExceeded
│   ├── ports/
│   │   ├── inbound.py                #   KernelPort: admit(context) → AdmissionDecision
│   │   └── outbound.py               #   AuditPort, MetricsPort, AlertPort
│   └── adapters/
│       ├── syscall_pipeline.py       #   7-stage pipeline (identity→capability→quota→policy→boundary→dispatch→audit)
│       ├── legacy_hooks.py           #   Legacy 7-check chain (src/core/hooks.py migration target)
│       ├── audit_chain.py            #   Hash-chained audit adapter
│       └── prometheus_metrics.py     #   Prometheus 14-family adapter
│
├── agent_execution/                  # SCREAMS: I run agents
│   ├── domain/
│   │   ├── agent.py                  #   Aggregate: Agent (manifest, spec, lifecycle state)
│   │   ├── process.py                #   Aggregate: AgentProcess (PID, phase, resource accounting)
│   │   ├── checkpoint.py             #   Value object: Checkpoint (state snapshot)
│   │   └── events.py                 #   AgentDeployed, AgentInvoked, AgentFailed, AgentPreempted
│   ├── ports/
│   │   ├── inbound.py                #   ExecutorPort: deploy(), invoke(), stop(), recover()
│   │   └── outbound.py               #   StackAdapterPort, RegistryPort, ProcessStorePort
│   ├── adapters/
│   │   ├── stacks/
│   │   │   ├── base.py               #     AgentStackAdapter ABC
│   │   │   ├── forgeos_adapter.py    #     ForgeOS native loop
│   │   │   ├── crewai_adapter.py     #     CrewAI SDK
│   │   │   ├── adk_adapter.py        #     Google ADK
│   │   │   ├── openclaw_adapter.py   #     OpenClaw HTTP gateway
│   │   │   └── sandbox_adapter.py    #     Docker / K8s sandbox
│   │   └── process_store.py          #     PostgreSQL process table adapter
│   └── api/
│       └── routes.py                 #   FastAPI routes: /agents CRUD, /invoke, /deploy
│
├── workflow_execution/               # SCREAMS: I execute multi-step workflows
│   ├── domain/
│   │   ├── workflow.py               #   Aggregate: Workflow, Step, ExecutionContext
│   │   ├── agentic_loop.py           #   Domain service: LLM → tool_use → result loop
│   │   └── events.py                 #   WorkflowStarted, StepCompleted, WorkflowFailed
│   ├── ports/
│   │   ├── inbound.py                #   WorkflowPort: run(), stream(), resume()
│   │   └── outbound.py               #   LLMPort, ToolPort, CheckpointPort
│   ├── adapters/
│   │   ├── llm_router.py             #   Anthropic/OpenAI routing + failover + streaming
│   │   └── cloud_tasks.py            #   GCP Cloud Tasks async execution
│   └── api/
│       └── routes.py                 #   FastAPI routes: /workflows
│
├── tool_execution/                   # SCREAMS: I execute tools safely
│   ├── domain/
│   │   ├── tool_call.py              #   Aggregate: ToolCall, ToolResult
│   │   └── events.py                 #   ToolExecuted, ToolFailed, ToolRetried
│   ├── ports/
│   │   ├── inbound.py                #   ToolPort: execute(call) → ToolResult
│   │   └── outbound.py               #   MCPServerPort, InternalHandlerPort
│   └── adapters/
│       ├── mcp_tool_executor.py      #   Routes mcp__* → MCP servers
│       ├── internal_tool_executor.py #   Routes company__* → in-process handlers
│       ├── mcp_server_manager.py     #   MCP server lifecycle
│       ├── client_mcp_manager.py     #   Per-client LRU MCP connections
│       └── providers/                #   CRM, GitHub, HTTP, Messaging MCP providers
│
├── scheduling/                       # SCREAMS: I schedule and trigger work
│   ├── domain/
│   │   ├── job.py                    #   Aggregate: ScheduledJob, CronRule
│   │   ├── trigger.py                #   Value object: Trigger (type, condition)
│   │   └── events.py                 #   JobFired, JobMissed, JobPaused
│   ├── ports/
│   │   ├── inbound.py                #   SchedulerPort: schedule(), cancel(), list()
│   │   └── outbound.py               #   JobStorePort, EventBusPort
│   └── adapters/
│       ├── apscheduler_adapter.py    #   APScheduler implementation
│       └── event_bus_adapter.py      #   Redis pub/sub event bus
│
├── agent_communication/              # SCREAMS: I coordinate agent-to-agent and agent-to-human
│   ├── domain/
│   │   ├── a2a_call.py               #   Aggregate: A2ACall (addressed, ACL-checked, depth-limited)
│   │   ├── approval_request.py       #   Aggregate: ApprovalRequest, ApprovalDecision (HITL)
│   │   └── events.py                 #   A2ACallMade, ApprovalRequested, ApprovalGranted
│   ├── ports/
│   │   ├── inbound.py                #   A2APort, HITLPort
│   │   └── outbound.py               #   AgentCallPort, NotificationPort, DurableEventPort
│   └── adapters/
│       ├── a2a_adapter.py            #   A2A protocol (cycle detection, ACL enforcement)
│       ├── a2h_gateway.py            #   A2H gateway (agent → human approval)
│       └── h2a_gateway.py            #   H2A gateway (human → agent response)
│
├── intelligence/                     # SCREAMS: I provide market and business intelligence
│   ├── domain/
│   │   ├── ontology.py               #   Domain ontology aggregate
│   │   ├── sync_event.py             #   Data sync domain events
│   │   └── lead.py                   #   Lead value object (BANT scoring)
│   ├── ports/
│   │   ├── inbound.py                #   IntelligencePort: sync(), query(), score()
│   │   └── outbound.py               #   ConnectorPort (CRM, GitHub, HTTP, Messaging)
│   └── adapters/
│       ├── crm_connector.py
│       ├── github_connector.py
│       ├── http_connector.py
│       └── messaging_connector.py
│
├── multi_tenancy/                    # SCREAMS: I isolate and manage tenants
│   ├── domain/
│   │   ├── tenant.py                 #   Aggregate: Tenant, Client
│   │   └── rls_policy.py             #   Value object: RLSPolicy
│   ├── ports/
│   │   ├── inbound.py                #   TenantPort: create(), switch(), enforce()
│   │   └── outbound.py               #   DatabasePort, SessionPort
│   └── adapters/
│       ├── postgres_rls.py           #   PostgreSQL RLS + connection pool
│       ├── session_store.py          #   In-memory / PostgreSQL sessions
│       └── redis_rate_limiter.py     #   Redis rate limiting
│
├── billing/                          # SCREAMS: I handle plans and payments
│   ├── domain/
│   │   ├── plan.py                   #   Aggregate: Plan, Subscription
│   │   └── events.py                 #   SubscriptionCreated, PaymentFailed
│   ├── ports/
│   │   ├── inbound.py                #   BillingPort: subscribe(), cancel(), invoice()
│   │   └── outbound.py               #   PaymentGatewayPort
│   └── adapters/
│       └── stripe_adapter.py         #   Stripe payment gateway
│
├── observability/                    # SCREAMS: I make the system visible
│   ├── domain/
│   │   ├── audit_event.py            #   Value object: AuditEvent (hash-chained)
│   │   └── metric.py                 #   Value object: Metric
│   ├── ports/
│   │   └── outbound.py               #   AuditPort, MetricPort, AlertPort
│   └── adapters/
│       ├── audit_store.py            #   Hash-chained audit log adapter
│       ├── prometheus_adapter.py     #   Prometheus metrics (14 families)
│       ├── grafana/                  #   Grafana dashboard configs
│       └── alerting.py               #   Slack, PagerDuty, log destinations
│
├── verticals/                        # SCREAMS: I am a specific business workload
│   ├── leadforge/                    #   B2B sales — agent_configs, workflows, knowledge
│   ├── dealforge/                    #   M&A analysis
│   ├── travelforge/                  #   Travel booking
│   ├── insureforge/                  #   Insurance processing
│   └── homeforge/                    #   Real estate
│   # Each vertical: own AgentConfig, Workflow, KnowledgeBase (DDD bounded contexts)
│
├── sdk/                              # PUBLIC SDK — thin client over ports
│   ├── manifest.py                   #   agent.yaml schema (forgeos/v1 + agentos/v1)
│   ├── agent.py                      #   Agent (declarative) + AgentBuilder (fluent)
│   ├── client.py                     #   ForgeOSClient HTTP wrapper
│   ├── kernel.py                     #   Kernel accessor
│   ├── cli.py                        #   forgeos CLI
│   └── runtime.py                    #   In-process singleton runtime
│
├── api/                              # ENTRYPOINT — FastAPI app (routes only, no logic)
│   ├── main.py                       #   App factory, middleware, lifespan
│   ├── dependencies.py               #   DI: inject ports, not implementations
│   └── routers/                      #   One router per vertical slice
│       ├── agents.py → agent_execution/api/routes.py
│       ├── workflows.py → workflow_execution/api/routes.py
│       ├── tools.py → tool_execution/
│       ├── approvals.py → agent_communication/
│       ├── intelligence.py → intelligence/
│       ├── billing.py → billing/
│       └── admin.py → multi_tenancy/
│
├── dashboard/                        # FRONTEND — Next.js 15 (feature-sliced)
│   └── src/
│       ├── features/                 #   One folder per business feature
│       │   ├── agents/               #     list, detail, create, invoke
│       │   ├── workflows/            #     list, detail, run
│       │   ├── approvals/            #     HITL approval queue
│       │   ├── environments/         #     Sandbox environments
│       │   ├── intelligence/         #     Market intelligence dashboards
│       │   ├── clients/              #     Multi-tenant client management
│       │   └── admin/                #     Audit, events, jobs, MCPs, skills, knowledge
│       └── shared/                   #   Cross-cutting: UI kit, hooks, lib
│           ├── components/
│           ├── hooks/
│           └── lib/
│
├── tests/                            # TESTS — organized by type
│   ├── unit/                         #   Pure domain tests, zero infra imports
│   │   └── (mirrors vertical structure)
│   ├── integration/                  #   Real DB + Redis, no mocks
│   │   └── (mirrors vertical structure)
│   ├── e2e/                          #   Full API flows
│   ├── conformance/                  #   Protocol tests (A2H, H2A, A2A contracts)
│   └── load/                         #   k6 performance tests
│
└── infra/                            # INFRA — unchanged
    ├── database/                     #   SQL migrations (001-007)
    ├── docker/                       #   Dockerfiles, docker-compose
    ├── terraform/gcp/                #   Cloud SQL, Redis, Cloud Run, VPC
    ├── k8s/                          #   Kubernetes + Kustomize overlays
    └── observability/                #   Grafana + Prometheus K8s configs
```

### Migration Path (Incremental, Zero Big-Bang)

Recommended order — each step ships independently without breaking existing tests:

1. **Extract `kernel/`** — move `src/platform/kernel.py`, `syscall.py`, `capabilities.py`, `audit.py`, `metrics.py`, `alerts.py` → define `KernelPort` inbound interface. All callers go through the port; adapters implement it. Tests stay green.
2. **Extract `tool_execution/`** — move `src/mcp/` into the new slice. Define `ToolPort` and `MCPServerPort`. Existing `tool_executor.py` becomes the adapter.
3. **Extract `agent_execution/`** — move `src/platform/executor.py`, `registry.py`, `process.py`, `checkpoint.py`, and `stacks/` under the new slice. Define `ExecutorPort` and `StackAdapterPort`.
4. **Extract `scheduling/`** — move `scheduler.py`, `triggers.py`, `event_bus.py`, `durable_event_store.py`.
5. **Extract `agent_communication/`** — move `a2a.py`, `a2a_contracts.py`, `a2h.py`, `h2a.py` (+ the `a2h/` package).
6. **Extract remaining slices** (`intelligence/`, `multi_tenancy/`, `billing/`, `observability/`).
7. **Collapse `api/`** — replace `src/dashboard/fastapi_app.py` with thin `api/main.py` + routers. Retire `src/dashboard/app.py` (Flask legacy).
8. **Reorganize `tests/`** — split flat `tests/` into `unit/`, `integration/`, `e2e/`, `conformance/`. Move `a2h/tests/` → `tests/conformance/`.
9. **Frontend feature slicing** — reorganize `dashboard/src/app/` → `dashboard/src/features/`.

### DDD Bounded Context Map

```
┌─────────────────────────────────────────────────────────────────┐
│                    ForgeOS Platform                              │
│                                                                  │
│  ┌────────────┐   ┌──────────────────┐   ┌───────────────────┐ │
│  │   kernel   │◄──│ agent_execution  │──►│ workflow_execution│ │
│  │ (upstream) │   │                  │   │                   │ │
│  └─────┬──────┘   └────────┬─────────┘   └────────┬──────────┘ │
│        │                   │                       │            │
│        ▼                   ▼                       ▼            │
│  ┌───────────┐   ┌──────────────────┐   ┌───────────────────┐  │
│  │observabil.│   │ tool_execution   │   │ agent_communic.   │  │
│  └───────────┘   └──────────────────┘   └───────────────────┘  │
│                                                                  │
│  ┌────────────┐   ┌──────────────┐   ┌──────────┐              │
│  │scheduling  │   │intelligence  │   │ billing  │              │
│  └────────────┘   └──────────────┘   └──────────┘              │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              multi_tenancy (shared kernel)               │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │    verticals/ — isolated bounded contexts per company    │   │
│  │    leadforge │ dealforge │ travelforge │ insureforge      │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Why This Enables Fast Agentic Maintainability

| Problem Today | How the Refactor Solves It |
|---|---|
| A new agent feature touches `executor.py` (30 KB), `kernel.py` (42 KB), `fastapi_app.py` (117 KB) | New feature lives entirely in `agent_execution/` — one folder, one bounded context |
| LLM routing mixed into `agentic_loop.py` and `claude_client.py` | `LLMPort` abstraction — swap provider in one adapter, no domain change |
| Tests are flat — unclear whether a test is unit, integration, or E2E | `tests/unit/` has zero infra imports and runs in milliseconds; `tests/integration/` needs DB |
| Adding a vertical (e.g., `homeforge2`) requires editing `src/companies/`, `tests/`, `examples/` in three places | New vertical is `verticals/homeforge2/` — self-contained bounded context |
| The `src/dashboard/fastapi_app.py` (117 KB) is an architectural monolith | Thin `api/routers/` delegate to ports — each router is <100 lines |
| Two admission paths (`hooks.py` and `syscall.py`) are entangled | `kernel/adapters/legacy_hooks.py` and `kernel/adapters/syscall_pipeline.py` are both adapters behind `KernelPort` — flag selects which adapter is injected |
