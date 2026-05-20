# Example Agents — How They Use the ForgeOS Platform

This guide explains every example agent in the repository: what it does, which ForgeOS platform features it exercises, and how the kernel governs its behavior at runtime.

## How to Read This Guide

Each agent entry shows:

- **What it does** — the business purpose
- **Platform features used** — which kernel/runtime capabilities are active
- **How the kernel governs it** — what gets checked, denied, or enforced

The platform features are:

| Feature | Symbol | What it means |
|---------|--------|---------------|
| Permission checks | `PERM` | Kernel checks tool whitelist + deny list before every tool call |
| Budget enforcement | `BUDGET` | Per-task and daily USD limits, two-phase reservation |
| Scheduling | `SCHED` | Cron-based trigger via SchedulerEngine |
| Event-driven | `EVENT` | Pub/sub trigger via EventBus |
| Always-on loop | `LOOP` | Continuous execution with configurable interval |
| Autonomous goal | `GOAL` | Goal-directed with `[GOAL_COMPLETE]` detection and iteration tracking |
| A2A protocol | `A2A` | Agent-to-agent calls across namespaces |
| Approval gates | `HITL` | Human-in-the-loop via `company__request_approval` |
| Knowledge base | `KB` | Reads from / writes to persistent knowledge store |
| Metrics | `METRIC` | Records KPIs via `company__record_metric` |
| Event publishing | `PUB` | Publishes events for other agents to consume |
| Checkpoints | `CKPT` | State saved at boundaries for crash recovery (autonomous agents) |
| MCP tools | `MCP` | External tool execution via Model Context Protocol servers |
| Audit trail | `AUDIT` | Every tool call and kernel decision recorded |
| Process table | `PROC` | PID, phase, resource accounting tracked |

Every agent gets `PERM`, `AUDIT`, and `PROC` automatically — the kernel enforces these on all tool calls regardless of configuration. The other features depend on the agent's manifest.

---

## ForgeOS Native Agents (`examples/forgeos/`)

### hello-forgeos
**Type:** reflex | **Tools:** none | **Features:** `PERM` `AUDIT` `PROC`

The simplest possible agent. No tools, no schedule — just a system prompt that responds to greetings. Demonstrates that an agent can run with zero tools; the kernel still tracks it in the process table and audits the invocation.

**Kernel behavior:** Permission checks return "no tools configured" (allow by default). Process table records tokens consumed and wallclock time.

### system-health-monitor
**Type:** always_on (120s loop) | **Tools:** `company__get_dashboard`, `company__publish_event` | **Features:** `PERM` `LOOP` `METRIC` `PUB` `AUDIT` `PROC`

Runs continuously, checking platform health every 2 minutes. Reads dashboard metrics and publishes events when anomalies are detected (e.g., `alert.fired`). Other event-driven agents (like `alert-responder`) subscribe to these events.

**Kernel behavior:** Each loop iteration goes through `_execute_tool()` with kernel gate. The dashboard tool is allowed; if the agent tried to call `shell.exec` it would be denied. The process table tracks cumulative tool calls across iterations. Budget enforcement applies if `_boundaries.budgets` is configured.

### daily-metrics-report
**Type:** scheduled (`0 9 * * *`) | **Tools:** `company__get_dashboard`, `company__record_metric` | **Features:** `PERM` `SCHED` `METRIC` `AUDIT` `PROC`

Triggered by the SchedulerEngine every morning at 9 AM. Reads the dashboard, computes trends, and records a metric snapshot. The scheduler calls `executor.invoke()` which binds the runtime — so the kernel gates every tool call even though the trigger is cron, not human.

**Kernel behavior:** Identical to a reflex invoke — the scheduler just automates the trigger. Permission checks, budget checks, audit trail all run normally.

### alert-responder
**Type:** event_driven | **Events:** `alert.fired`, `cost.exceeded` | **Tools:** `company__query_events`, `company__resolve_event`, `company__request_approval` | **Features:** `PERM` `EVENT` `HITL` `AUDIT` `PROC`

Subscribes to the EventBus. When `system-health-monitor` publishes an `alert.fired` event, the EventBus invokes this agent. For high-severity alerts, it uses `company__request_approval` to escalate to a human — the HITL gate pauses execution until approval is granted.

**Kernel behavior:** The `company__request_approval` tool triggers the kernel's `ask_human` decision path. If the agent had this tool in its deny list, the kernel would block the escalation entirely — a misconfiguration that the AdmissionController would warn about at deploy time.

### qa-knowledge-assistant
**Type:** reflex | **Tools:** `company__search_knowledge` | **Features:** `PERM` `KB` `AUDIT` `PROC`

On-demand Q&A agent. Searches the knowledge base and synthesizes answers. The simplest useful agent — one tool, invoked by API call, returns text.

**Kernel behavior:** Single permission check per invocation. If budgets are configured, the kernel checks `per_task_usd` before the search tool runs.

---

## CrewAI Agents (`examples/crewai/`)

CrewAI agents add the role/goal/backstory pattern. The kernel gates are inside `BaseTool._run()` — every tool call passes through `runtime.check_tool()` even when CrewAI's native `Crew.kickoff()` is running.

### hello-crewai
**Type:** reflex | **Tools:** none | **Metadata:** `crewai_role="Greeter"` | **Features:** `PERM` `AUDIT` `PROC`

Greeting agent with CrewAI role pattern. If the CrewAI SDK is installed, runs via `Crew.kickoff()` with a single-task crew. Otherwise falls back to ForgeOS native loop. Either way, kernel governs identically.

### crew-support-agent
**Type:** always_on (60s loop) | **Tools:** `company__query_events`, `company__publish_event` | **Metadata:** role/goal/backstory | **Features:** `PERM` `LOOP` `PUB` `EVENT` `AUDIT` `PROC`

Continuous support ticket triage. Polls for new events every 60 seconds, routes tickets, publishes resolution events. The CrewAI backstory ("You are a veteran support specialist...") gives the LLM context for prioritization decisions.

**Kernel behavior:** Each `_run()` call in the BaseTool wrapper checks permissions. The 60-second loop means ~1440 permission checks per day — all logged in the audit trail.

### crew-content-creator
**Type:** scheduled (`0 8 * * 1-5`) | **Tools:** `company__search_knowledge`, `company__record_metric` | **Features:** `PERM` `SCHED` `KB` `METRIC` `AUDIT` `PROC`

Weekday morning content drafting. Searches knowledge base for topic ideas, writes content, records output metrics. CrewAI's task-oriented structure maps well to a "research → draft → review" workflow.

### crew-deal-analyst
**Type:** event_driven | **Events:** `deal.created`, `deal.updated` | **Tools:** `company__search_knowledge`, `company__record_metric` | **Features:** `PERM` `EVENT` `KB` `METRIC` `AUDIT` `PROC`

Real-time deal scoring. Triggered when a new deal is created or an existing one updated. Searches knowledge base for comparable deals, scores the opportunity, records the score as a metric.

### crew-market-researcher
**Type:** autonomous (goal-directed) | **Goal:** "Research competitor pricing" | **Max iterations:** 8 | **Tools:** `company__search_knowledge`, `company__add_decision` | **Features:** `PERM` `GOAL` `CKPT` `KB` `AUDIT` `PROC`

Multi-iteration competitive research. The executor runs an autonomous loop: invoke → check for `[GOAL_COMPLETE]` → save checkpoint → repeat. If the agent crashes at iteration 5, it resumes from the last checkpoint with the crash count incremented.

**Kernel behavior:** Budget enforcement is critical here — without a `daily_usd` limit, an autonomous agent could run indefinitely. The process table tracks cumulative tokens and dollars across all iterations. The executor saves checkpoints via `_save_checkpoint()` at each loop boundary.

### crew-strategic-advisor
**Type:** reflex | **Tools:** `company__search_knowledge` | **Features:** `PERM` `KB` `AUDIT` `PROC`

On-demand strategic guidance with CrewAI role pattern ("You are a senior strategy consultant..."). Single-turn interaction.

---

## Google ADK Agents (`examples/adk/`)

ADK agents use `FunctionTool` async wrappers. The kernel gate runs inside each wrapper before `tool_executor.execute()`. When the ADK SDK is installed, agents run via `Runner.run_async()` with `InMemorySessionService`.

### hello-adk
**Type:** reflex | **Tools:** none | **Features:** `PERM` `AUDIT` `PROC`

ADK hello world. If `google-adk` is installed, runs through real ADK Runner. Otherwise falls back to ForgeOS native loop.

### always-on-workflow-engine
**Type:** always_on (90s loop) | **Tools:** `company__query_events`, `company__publish_event`, `company__get_dashboard` | **Features:** `PERM` `LOOP` `PUB` `METRIC` `AUDIT` `PROC`

Workflow orchestration engine. Polls for events every 90 seconds, routes them to appropriate handlers, publishes status updates. The ADK adapter creates a `Runner` per agent with `InMemorySessionService` for multi-turn state.

### scheduled-compliance-check
**Type:** scheduled (`0 6 * * *`) | **Tools:** `company__search_knowledge`, `company__record_metric` | **Features:** `PERM` `SCHED` `KB` `METRIC` `AUDIT` `PROC`

Daily 6 AM compliance audit. Searches for policy documents, evaluates current state against policies, records compliance score. The ADK `FunctionTool` wrappers check permissions before each knowledge base query.

### event-driven-invoice-processor
**Type:** event_driven | **Events:** `invoice.received`, `payment.failed` | **Tools:** `company__record_metric`, `company__request_approval` | **Features:** `PERM` `EVENT` `HITL` `METRIC` `AUDIT` `PROC`

Billing automation with human-in-the-loop. When an invoice arrives, records the amount; when payment fails, escalates to human approval via `company__request_approval`. The HITL gate is particularly important here — the agent should never auto-retry failed payments without human confirmation.

**Kernel behavior:** The `company__request_approval` tool triggers the `ask_human` code path. If the agent's budget is exceeded, the kernel blocks the tool call with a `rate_limit` decision before the approval request is even sent.

### autonomous-report-writer
**Type:** autonomous (goal-directed) | **Goal:** "Compile quarterly business review" | **Max iterations:** 12 | **Tools:** `company__get_dashboard`, `company__search_knowledge`, `company__add_decision` | **Features:** `PERM` `GOAL` `CKPT` `KB` `METRIC` `AUDIT` `PROC`

Multi-iteration report generation. Gathers dashboard data, searches knowledge base for context, records decisions, iterates until the report is complete. At each iteration boundary, the executor saves a checkpoint with the current step index and crash count.

**Kernel behavior:** This is the most governance-intensive example: 12 possible iterations × 3 tools = up to 36 kernel permission checks. Budget limits prevent runaway costs. Checkpoints enable crash recovery. The process table tracks cumulative resource usage across all iterations.

### reflex-data-analyst
**Type:** reflex | **Tools:** `company__get_dashboard`, `company__search_knowledge` | **Features:** `PERM` `KB` `METRIC` `AUDIT` `PROC`

On-demand data analysis. The ADK Runner manages the multi-turn conversation via `InMemorySessionService`, while the kernel gates each tool call through the FunctionTool wrapper.

### full_platform_adk_agent.py
**Type:** Python script | **Features:** ALL 9 capabilities

The comprehensive demo. Deploys 3 agents (research-analyst, finance-approver, data-enricher), then runs a research workflow exercising every platform capability from inside the agent context. See `examples/adk/full_platform_adk_agent.py` for the full walkthrough.

---

## OpenClaw Agents (`examples/openclaw/`)

OpenClaw agents use SOUL.md for personality and a local `ToolProxyServer` for kernel-gated tool execution. When the Node.js gateway is running, tool calls go through `POST /tool` with token validation. Otherwise, they fall back to the ForgeOS agentic loop.

### hello-openclaw
**Type:** reflex | **Tools:** none | **Features:** `PERM` `AUDIT` `PROC`

SOUL-pattern greeting agent. The SOUL.md defines the agent's personality; AGENTS.md holds metadata. No tools, so the ToolProxyServer is idle.

### always-on-guardian
**Type:** always_on (300s loop) | **Heartbeat:** 5 minutes | **Tools:** `company__get_dashboard`, `company__publish_event` | **Features:** `PERM` `LOOP` `PUB` `AUDIT` `PROC`

System guardian with heartbeat monitoring. The `HEARTBEAT.md` file configures the 5-minute interval. On each heartbeat, the agent reads dashboard metrics and publishes anomaly events. The ToolProxyServer validates the agent's token and checks kernel permissions before each tool execution.

### scheduled-inbox-checker
**Type:** scheduled (`*/30 8-18 * * 1-5`) | **Tools:** `company__publish_event`, `company__record_metric` | **Features:** `PERM` `SCHED` `PUB` `METRIC` `AUDIT` `PROC`

Business-hours inbox monitoring every 30 minutes. The scheduler triggers the OpenClaw adapter, which either routes through the gateway or falls back to the platform loop. Either way, the kernel gates tool calls.

### event-driven-notification-handler
**Type:** event_driven | **Events:** `user.signup`, `task.completed`, `error.critical` | **Tools:** `company__query_events`, `company__publish_event` | **Features:** `PERM` `EVENT` `PUB` `AUDIT` `PROC`

Event-to-notification routing. Subscribes to three event types and publishes formatted notifications. The SOUL.md contains routing rules in markdown that the LLM follows.

### autonomous-knowledge-builder
**Type:** autonomous (goal-directed) | **Goal:** "Build structured knowledge base" | **Max iterations:** 15 | **Tools:** `company__search_knowledge`, `company__add_decision` | **Features:** `PERM` `GOAL` `CKPT` `KB` `AUDIT` `PROC`

Iterative knowledge discovery. Searches existing knowledge, identifies gaps, records new decisions. At 15 max iterations, this is the longest-running autonomous agent — budget enforcement and checkpoints are essential.

**Kernel behavior:** Each tool call goes through the ToolProxyServer (if gateway is running) or `_execute_tool` (fallback). The token scoped to this agent's tools prevents it from calling any tool not in its manifest. Checkpoints at each iteration enable crash recovery.

### reflex-assistant
**Type:** reflex | **Tools:** none | **Features:** `PERM` `AUDIT` `PROC`

SOUL-pattern structured reasoning. Uses ReAct (Think → Act → Observe) loop described in SOUL.md. No external tools — pure LLM reasoning.

---

## A2A Agents (`examples/a2a/`)

These agents use the Agent-to-Agent protocol — `agent__call`, `agent__async_call`, `agent__await`, `agent__list_available`. The kernel checks A2A permissions (namespace ACLs, depth limits, cycle detection) on every call.

### ceo-supervisor
**Type:** reflex | **Stack:** forgeos | **Tools:** `agent__call`, `agent__list_available`, `company__get_dashboard` | **Features:** `PERM` `A2A` `METRIC` `AUDIT` `PROC`

CEO-level orchestrator. Discovers available agents via `agent__list_available`, then delegates tasks via `agent__call`. The kernel enforces A2A ACLs: the callee's `spec.capabilities.a2a.canBeCalledBy` must permit this caller. Cross-namespace calls without explicit ACL are denied.

### escalation-router
**Type:** event_driven | **Stack:** adk | **Events:** `ticket.created`, `ticket.escalated` | **Tools:** `agent__call`, `agent__list_available`, `company__query_events` | **Features:** `PERM` `EVENT` `A2A` `AUDIT` `PROC`

Smart ticket routing. When a ticket event fires, discovers available specialists and routes the ticket to the best match via A2A call. The kernel's cycle detection prevents routing loops (A → B → A).

### research-coordinator
**Type:** reflex | **Stack:** crewai | **Tools:** `agent__call`, `agent__async_call`, `agent__await`, `company__add_decision` | **Features:** `PERM` `A2A` `KB` `AUDIT` `PROC`

Parallel research orchestration. Fires multiple `agent__async_call` requests to different researchers, then `agent__await` gathers results. The kernel tracks A2A depth (max 5 levels by default) and prevents infinite delegation chains.

### review-pipeline
**Type:** event_driven | **Stack:** forgeos | **Events:** `draft.submitted` | **Tools:** `agent__call`, `company__request_approval` | **Features:** `PERM` `EVENT` `A2A` `HITL` `AUDIT` `PROC`

Review → approval workflow. When a draft is submitted, calls a reviewer agent via A2A, then gates the final approval through `company__request_approval` (HITL).

---

## Advanced Agents (`examples/advanced/`)

### multi-agent-debate
**Type:** reflex | **Stack:** forgeos | **Tools:** `agent__call`, `agent__async_call`, `agent__await` | **Features:** `PERM` `A2A` `AUDIT` `PROC`

Parallel debate facilitation. Spawns multiple agents with opposing viewpoints via async A2A calls, awaits their arguments, then synthesizes a conclusion.

### self-improving-agent
**Type:** autonomous (goal-directed) | **Stack:** forgeos | **Goal:** "Analyze performance and improve" | **Max iterations:** 10 | **Tools:** `company__search_knowledge`, `company__add_decision`, `company__get_metric`, `company__record_metric` | **Features:** `PERM` `GOAL` `CKPT` `KB` `METRIC` `AUDIT` `PROC`

Self-optimization loop. Reads its own performance metrics, identifies weaknesses, records improvement decisions, and iterates. Checkpoints save the improvement history so a restart doesn't lose progress.

---

## Filesystem Agents (`examples/filesystem/`)

These agents use MCP filesystem tools — `mcp__filesystem__read_file`, `mcp__filesystem__list_directory`, `mcp__filesystem__write_file`. Requires a connected MCP filesystem server.

### config-validator
**Type:** reflex | **Stack:** forgeos | **Tools:** `mcp__filesystem__read_file`, `company__publish_event` | **Features:** `PERM` `MCP` `PUB` `AUDIT` `PROC`

Reads YAML/JSON config files and validates them. Publishes validation events. The kernel's permission check verifies `mcp__filesystem__read_file` is in the agent's allowed tools before the MCP server is contacted.

### file-summarizer
**Type:** reflex | **Stack:** forgeos | **Tools:** `mcp__filesystem__read_file`, `company__add_decision` | **Features:** `PERM` `MCP` `KB` `AUDIT` `PROC`

Reads files and generates summaries stored in the knowledge base.

### log-analyzer
**Type:** reflex | **Stack:** adk | **Tools:** `mcp__filesystem__read_file`, `mcp__filesystem__list_directory`, `company__record_metric` | **Features:** `PERM` `MCP` `METRIC` `AUDIT` `PROC`

Scans log directories for errors and anomalies. Records findings as metrics. The ADK FunctionTool wrappers gate both MCP calls through the kernel.

### report-writer
**Type:** scheduled (`0 10 * * *`) | **Stack:** crewai | **Tools:** `mcp__filesystem__write_file`, `company__get_dashboard` | **Features:** `PERM` `SCHED` `MCP` `METRIC` `AUDIT` `PROC`

Daily morning report written to disk. The kernel checks `mcp__filesystem__write_file` permission — a sensitive tool that could overwrite arbitrary files. The agent's tool whitelist restricts which write operations are allowed.

---

## Google Workspace Agents (`examples/google-workspace/`)

These agents use MCP Google Workspace tools — Gmail, Drive, Calendar. Requires a connected Google Workspace MCP server.

### email-triage
**Type:** scheduled (`*/30 8-20 * * 1-5`) | **Tools:** Gmail search/read/draft/label, `company__request_approval`, `company__record_metric`, `company__publish_event`, `company__search_knowledge` | **Features:** `PERM` `SCHED` `MCP` `HITL` `METRIC` `PUB` `KB` `AUDIT` `PROC`

The most tool-rich agent. Runs every 30 minutes during business hours, triages unread emails, drafts responses, and escalates sensitive messages via approval gate. The kernel checks 8 different tool names — a mix of MCP and platform tools — on every invocation.

**Kernel behavior:** This is the strongest test of permission enforcement. The agent has wildcard access to Gmail tools but NOT to Drive tools. If it tried `mcp__google-workspace__search_drive_files`, the kernel would deny it. Budget limits prevent excessive Gmail API calls.

### calendar-prep
**Type:** scheduled (`0 7 * * 1-5`) | **Tools:** Calendar events, Drive search/read, knowledge search, filesystem write, metric recording | **Features:** `PERM` `SCHED` `MCP` `KB` `METRIC` `AUDIT` `PROC`

Morning meeting preparation. Reads today's calendar, pulls relevant Drive docs, searches knowledge base for context, writes a briefing file, records metrics.

### drive-finder
**Type:** reflex | **Tools:** Drive search/read/share, knowledge search, decision recording | **Features:** `PERM` `MCP` `KB` `AUDIT` `PROC`

On-demand Google Drive search. Finds files, reads content, generates shareable links, records findings in knowledge base.

### email-followup-tracker
**Type:** scheduled (`0 17 * * 1-5`) | **Tools:** Gmail search/thread/draft, approval gate, metric recording, knowledge search | **Features:** `PERM` `SCHED` `MCP` `HITL` `METRIC` `KB` `AUDIT` `PROC`

End-of-day follow-up detection. Identifies emails awaiting responses, drafts follow-ups, escalates overdue items via approval gate.

---

## Mixed Agents (`examples/mixed/`)

Multi-tool agents that combine platform, company, and external tools.

### budget-guardian
**Type:** always_on (300s loop) | **Tools:** dashboard, events, approval, metrics | **Features:** `PERM` `LOOP` `HITL` `PUB` `METRIC` `AUDIT` `PROC`

Budget monitoring with escalation thresholds. Reads spending metrics, publishes warnings at 80%, escalates to human approval at 95%. The kernel's own `BudgetManager` enforces the guardian's own budget separately from the budgets it monitors.

### compliance-auditor
**Type:** scheduled (`0 6 * * 1`) | **Stack:** adk | **Tools:** knowledge, events, metrics, approval, dashboard | **Features:** `PERM` `SCHED` `KB` `HITL` `METRIC` `AUDIT` `PROC`

Weekly Monday morning compliance audit. Searches policies, queries recent events, checks for violations, escalates non-compliant items. The ADK Runner manages multi-turn conversation state across the audit steps.

### full-stack-sales
**Type:** always_on (180s loop) | **Tools:** CRM (search/update/activity), knowledge, approval, metrics, events | **Features:** `PERM` `LOOP` `HITL` `KB` `PUB` `METRIC` `AUDIT` `PROC`

Lead-to-close pipeline automation. Searches CRM for new leads, qualifies them, creates follow-up activities, escalates high-value deals via approval gate. Uses 7 tools — the kernel checks each one.

### onboarding-assistant
**Type:** event_driven | **Stack:** crewai | **Events:** `employee.joined`, `employee.role_assigned` | **Tools:** knowledge, messaging, events, metrics | **Features:** `PERM` `EVENT` `KB` `PUB` `METRIC` `AUDIT` `PROC`

Employee onboarding orchestration. Triggered when HR creates a new employee record. Sends welcome messages, publishes onboarding events, records completion metrics. CrewAI's role pattern gives the agent an "HR specialist" persona.

---

## Platform Integration Agents (`examples/platform/`)

Agents using external platform tools (CRM, ads, insurance, GitHub, HTTP, MLS).

### lead-qualifier
**Type:** scheduled (hourly) | **Tools:** `platform__crm_search_leads`, `platform__crm_update_lead`, `company__record_metric` | **Features:** `PERM` `SCHED` `METRIC` `AUDIT` `PROC`

BANT lead scoring every hour. Searches CRM for unscored leads, applies scoring criteria, updates lead records.

### ad-campaign-optimizer
**Type:** scheduled (daily) | **Stack:** crewai | **Tools:** `platform__ads_get_campaigns`, `platform__ads_update_bid`, `company__record_metric` | **Features:** `PERM` `SCHED` `METRIC` `AUDIT` `PROC`

Daily ad performance optimization. The CrewAI role pattern gives it a "performance marketing specialist" persona. Reads campaign data, adjusts bids, records performance metrics.

### pr-reviewer
**Type:** event_driven | **Events:** `pr.opened`, `pr.updated` | **Tools:** `platform__github_get_pr`, `platform__github_create_review`, `company__publish_event` | **Features:** `PERM` `EVENT` `PUB` `AUDIT` `PROC`

Automated code review. Triggered when a PR is opened or updated. Reads the diff, creates a review, publishes the result as an event.

### insurance-comparator
**Type:** reflex | **Stack:** openclaw | **Tools:** `platform__insurance_get_quotes`, `platform__insurance_compare_rates`, `company__add_decision` | **Features:** `PERM` `KB` `AUDIT` `PROC`

Insurance quote comparison. The OpenClaw SOUL.md guides structured comparison logic. Results recorded as decisions in the knowledge base.

### property-scout
**Type:** reflex | **Stack:** adk | **Tools:** `platform__mls_search_listings`, `platform__mls_get_listing`, `company__search_knowledge` | **Features:** `PERM` `MCP` `KB` `AUDIT` `PROC`

MLS property search. The ADK Runner handles multi-turn property filtering while the kernel gates each MLS API call.

### web-data-fetcher
**Type:** reflex | **Tools:** `platform__http_fetch`, `company__add_decision`, `company__search_knowledge` | **Features:** `PERM` `KB` `AUDIT` `PROC`

Web content extraction. Fetches URLs, extracts structured data, stores findings. The `platform__http_fetch` tool is particularly sensitive — the kernel's permission check prevents the agent from fetching arbitrary URLs not in its allowed scope.

---

## Demo Scripts

### full_platform_demo.py
Deploys 2 agents and exercises all 9 kernel/runtime capabilities in ~0.5 seconds. No API keys needed.

```bash
PYTHONPATH=. python3 examples/full_platform_demo.py
```

### full_platform_adk_agent.py
Deploys 3 ADK agents across 2 namespaces with a multi-step research workflow. Exercises all 9 capabilities from the ADK agent context.

```bash
PYTHONPATH=. python3 examples/adk/full_platform_adk_agent.py
```

### deploy.py / run_all_hello_world.py
Utility scripts for deploying agents to a running ForgeOS instance. Requires the platform to be booted first.

```bash
# Boot platform
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000

# Deploy hello-world agents (all 4 stacks)
PYTHONPATH=. python3 examples/run_all_hello_world.py
```

---

## Platform Feature Coverage Matrix

| Feature | ForgeOS | CrewAI | ADK | OpenClaw | A2A | Advanced | Filesystem | Workspace | Mixed | Platform |
|---------|---------|--------|-----|----------|-----|----------|------------|-----------|-------|----------|
| `PERM` | 5/5 | 6/6 | 7/7 | 6/6 | 4/4 | 2/2 | 4/4 | 4/4 | 4/4 | 6/6 |
| `SCHED` | 1 | 1 | 1 | 1 | - | - | 1 | 3 | 1 | 2 |
| `EVENT` | 1 | 1 | 1 | 1 | 2 | - | - | - | 1 | 1 |
| `LOOP` | 1 | 1 | 1 | 1 | - | - | - | - | 2 | - |
| `GOAL` | - | 1 | 1 | 1 | - | 1 | - | - | - | - |
| `A2A` | - | - | - | - | 4 | 1 | - | - | - | - |
| `HITL` | 1 | - | 1 | - | 1 | - | - | 2 | 2 | - |
| `KB` | 1 | 3 | 3 | 2 | 1 | 1 | 2 | 3 | 2 | 3 |
| `METRIC` | 1 | 2 | 3 | 1 | - | 1 | 1 | 3 | 3 | 2 |
| `MCP` | - | - | - | - | - | - | 4 | 4 | - | - |
| `CKPT` | - | 1 | 1 | 1 | - | 1 | - | - | - | - |
