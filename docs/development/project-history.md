# Project History

Helios OS was built over 5 weeks (March 8 -- April 12, 2026) through iterative development sessions using Claude Code. Each phase added a major architectural layer on top of the previous one.

## Timeline

```
March 8-21   [Phase 1]  Single-company agent swarm (LeadForge)
March 21-28  [Phase 2]  Multi-tenant SaaS platform (5 companies)
March 28-Apr 7  [Phase 3]  Multi-stack agent platform (4 adapters)
April 7-12   [Phase 4]  Production hardening + dashboard polish
```

---

## Phase 1: AI-Operated Company (March 8-21)

**Starting prompt:** *"I would like to design 100% digital company using agent swarms of Claude and Claude Code. I would like to have a superdetailed design of how a company could operate like that."*

**What was built:**
- The core concept: an AI-operated company where agents are employees
- **LeadForge AI** -- a B2B lead generation agency with 26 agents across 7 departments (Executive, Sales, Marketing, Operations, Finance, HR, Legal)
- Three-tier agent hierarchy: Executives (Opus) -> Department Leads (Opus) -> Workers (Sonnet/Haiku)
- The `AgentInvoker` orchestration engine with delegation
- Seven-check governance hook chain (budget, rate limit, auth, cost, compliance, Slack, audit)
- MCP tool integration for external services
- Event bus for cross-department communication
- HITL (Human-in-the-Loop) approval gateway
- Knowledge base with decision precedent storage
- Flask-based dashboard with approval management
- In-memory and PostgreSQL database backends

**Key design decisions:**
- Agents organized by department, not by function -- mirrors a real company org chart
- Strict tier enforcement: workers cannot spawn sub-agents (prevents runaway delegation)
- Budget pre-checks block API calls when cost approaches limit
- All tools go through a single `ToolExecutor` for audit trail

**Visual documentation created:** `leadforge-visual.html`, `leadforge-day.html`, `leadforge-team.html`, `leadforge-platform.html`

---

## Phase 2: Multi-Tenant SaaS (March 21-28)

**Key question:** *"For which kind of company could I test the full system?"*

**What was built:**
- Five company packages: LeadForge (B2B sales), DealForge (M&A), TravelForge (travel), InsureForge (insurance), HomeForge (real estate)
- Multi-tenant database layer with PostgreSQL Row-Level Security
- Firebase authentication (JWT + API keys + RBAC)
- Stripe billing integration (4 tiers: Trial/Starter/Growth/Enterprise)
- GCP infrastructure via Terraform (Cloud SQL, Cloud Run, Redis, VPC, Secret Manager)
- Per-tenant API key management via GCP Secret Manager
- Usage enforcement with daily token budgets and monthly cost limits
- Docker containerization with Cloud Build CI/CD

**Key design decisions:**
- Each company is a pluggable package under `src/companies/` -- same framework, different agent configs
- RLS enforced at database layer (`set_config('app.current_tenant', ...)`) -- not application-level filtering
- Graceful degradation: no API key -> simulation, no DB -> in-memory, no Redis -> in-memory rate limiting

**Visual documentation created:** `forgeos-platform.html`, `forgeos-summary.html`, `forgeos-capacity.html`

---

## Phase 3: Multi-Stack Platform (March 28 -- April 7)

**Key question:** *"How is this good compared with other models like ADK, CrewAI, or OpenClaw?"*

**Answer: don't compete -- support all of them.**

**What was built:**
- The `AgentStackAdapter` interface in `stacks/base.py` -- the universal adapter contract
- Four stack implementations:
  - **Helios OS native** -- the existing agentic loop, now wrapped as an adapter
  - **CrewAI** -- real SDK integration with `Crew.kickoff()` and `BaseTool` wrapping
  - **Google ADK** -- real SDK integration with `Runner.run_async()` and `FunctionTool` wrapping
  - **OpenClaw** -- gateway subprocess management with HTTP communication
- The **Platform Layer** (`src/platform/`):
  - `AgentRegistry` -- universal agent store across all stacks
  - `PlatformExecutor` -- central dispatcher (deploy, invoke, recover)
  - `SchedulerEngine` -- cron-based scheduling
  - `EventBus` -- pub/sub for event-driven agents
  - `LLMRouter` -- multi-provider routing with retry, failover, and streaming
- The **agentic loop** (`agentic_loop.py`) -- shared LLM -> tool_use -> execute -> LLM loop
- Five execution types: always_on, scheduled, event_driven, reflex, autonomous
- Three ownership types: personal, shared, client
- **Next.js dashboard** replacing the Flask HTML -- agent management, admin chat, AI wizard
- 50+ agents deployed across all stacks
- MCP server integration (filesystem, Google Drive attempt)
- Agent creation wizard (conversational AI-assisted agent design)

**Key design decisions:**
- All adapters fall back to Helios OS native when SDK is missing -- zero-dependency baseline
- The platform layer is stack-agnostic -- same registry, executor, scheduler for all agents
- Tools are bridged to each framework's native format (BaseTool for CrewAI, FunctionTool for ADK)
- The agentic loop handles both sync (`AgentResult`) and streaming (`SSE events`)

**Visual documentation created:** `forgeos-complete-summary.html`, `forgeos-infrastructure-stack.html`, `forgeos-system-summary.html`, `forgeos-candidate-agents.html`

---

## Phase 4: Production Hardening (April 7-12)

**Key request:** *"Can you do a very deep review of all the code and look for potential problems when deploying agents and not connecting to MCPs or other elements?"*

**What was built/fixed:**

### Multi-Turn Agent Chat
- SSE streaming chat endpoint with session management
- Conversation history injected into agentic loop
- Inline HITL approval cards in chat stream

### "Edit with AI" Wizard
- Existing agents can be brought back to the AI wizard for iterative improvement
- Wizard loads current config and offers conversational editing

### PostgreSQL Persistence
- Docker Compose setup for local Postgres (port 5433)
- Migration runner applies 5 SQL files on boot
- All stores auto-switch from in-memory to PostgreSQL when DATABASE_URL is set

### Dashboard Polish
- OpenAI-inspired dark theme (near-black sidebar, teal accent)
- Admin chat with command handlers (list agents, system status)
- Provider status page
- Neutral gray badges for most statuses (only running=teal, failed=red)

### Production Audit (28 fixes)
Three rounds of deep code review found and fixed:
- **Boot hangs:** MCP `connect_all()` wrapped in 30s timeout; client MCP `initialize()` wrapped in 10s timeout
- **Memory leaks:** Session eviction (2h TTL, 10K cap) with background cleanup task
- **Race conditions:** Atomic session creation via `setdefault()`, per-session locking in executor, gateway start serialized via `asyncio.Lock`
- **Crash loops:** `QUARANTINED` agent status after repeated crashes (blocks auto-recovery)
- **Silent failures:** Tool validation at deploy time, tool_executor null checks, stream error guarantees
- **Data corruption:** History list copying to prevent mutation, wildcard support in tool whitelist
- **Gateway issues:** OpenClaw subprocess leak cleanup, double-start prevention, response validation
- **ADK bug:** `_invoke_via_platform()` was referencing undefined `history` variable
- **Tool filtering:** Empty filter results now return empty list (not all tools)
- **LLM Router:** Streaming sentinel race fix, error field on LLMResponse, schema validation

---

## Conversation Statistics

| Session | Dates | Messages | Size | Topics |
|---------|-------|----------|------|--------|
| e280ee82 | Mar 8-21 | ~350 | 2.5 MB | Initial design, company concept |
| a89adcf4 | Mar 21-28 | ~3,400 | 26 MB | Multi-tenant SaaS, 5 companies |
| d7e18ee6 | Mar 28-Apr 7 | ~2,200 | 17 MB | Multi-stack platform, dashboard |
| c7ccd075 | Apr 7-12 | ~6,400 | 28 MB | Production hardening, audits |
| **Total** | **5 weeks** | **~12,350** | **74 MB** | |

---

## Architecture Evolution

```
v1 (Phase 1): Single company, single stack, Flask dashboard
              src/core/ + src/companies/leadforge/ + src/mcp/

v2 (Phase 2): Multi-tenant, multi-company, SaaS infrastructure
              + src/api/ + src/billing/ + infrastructure/terraform/

v3 (Phase 3): Multi-stack, platform layer, Next.js dashboard
              + stacks/ + src/platform/ + dashboard/

v3.1 (Phase 4): Production-hardened, 28 bug fixes, streaming chat
              + session management + cost tracking + audit + alerts
```

Each phase added a new layer without breaking the previous ones. The v1 code still works as the "legacy subsystem" within the v3 platform.
