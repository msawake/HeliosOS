# ForgeOS — Use Cases

ForgeOS is an AI agent operating system. You declare agents as YAML manifests, deploy them with the CLI, and the platform handles scheduling, LLM routing, tool execution, persistence, and governance. Agents are the programs; ForgeOS is the OS they run on.

---

## How ForgeOS Works

```
You write an agent.yaml
       ↓
forgeos deploy agent.yaml   ← validates manifest, registers agent
       ↓
Platform assigns an AgentProcess with a stable PID
       ↓
Executor fires the agent based on its execution type:
  • reflex       → waits for an invoke call
  • scheduled    → fires on a cron expression
  • always_on    → runs a loop forever (sleeps between iterations)
  • event_driven → wakes on a matching pub/sub event
  • autonomous   → self-directs toward a goal until [GOAL_COMPLETE]
       ↓
Each run: LLM call → tool_use → tool_result → LLM loop
       ↓
Kernel enforces budgets, data boundaries, tool ACLs, HITL approvals
       ↓
Audit trail written, metrics emitted, optional A2A calls dispatched
```

The five stacks (forgeos, crewai, adk, openclaw, sandbox) are interchangeable — you pick one per agent, and the platform wraps it in the same lifecycle contract.

---

## Execution Types Reference

| Type | Trigger | Use When |
|---|---|---|
| `reflex` | API call / CLI invoke | On-demand tasks where a human or another agent drives timing |
| `scheduled` | Cron expression | Periodic batch jobs with predictable timing |
| `always_on` | Continuous loop | 24/7 background processes that must never miss an event |
| `event_driven` | Named pub/sub event | Reactive workflows triggered by activity in other systems |
| `autonomous` | Deployed once, self-loops | Goal-directed work that plans and executes its own steps |

---

## Use Case 1 — On-Demand Q&A / Reflex Agent

**Scenario**: A team needs a shared assistant they can invoke any time to answer questions, draft content, or analyze data. No schedule needed — it runs when called.

**How it works**:
1. User calls `POST /api/platform/agents/qa-assistant/invoke` with a prompt.
2. Platform routes to the agent's LLM.
3. Agent reads from tools (knowledge base, CRM, filesystem) and returns a structured response.
4. Response is logged in the audit trail and returned synchronously.

**Manifest**:
```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: qa-assistant
  description: "On-demand assistant for internal questions, drafts, and lookups."
  department: operations
spec:
  stack: forgeos
  execution_type: reflex
  ownership: shared
  llm:
    chat_model: claude-sonnet-4-6
    provider: anthropic
  tools:
    - company__search_knowledge
    - platform__file_read
  system_prompt: |
    You answer internal team questions. Search the knowledge base first,
    then the filesystem if needed. Be concise and cite your sources.
```

**CLI**:
```bash
forgeos deploy qa-assistant.yaml
forgeos invoke qa-assistant "What is our refund policy for enterprise clients?"
```

---

## Use Case 2 — Hourly Lead Qualification (Scheduled)

**Scenario**: Sales ops wants every new CRM lead scored against BANT criteria and updated automatically — no human needs to run this.

**How it works**:
1. At the top of each hour, the platform's `SchedulerEngine` fires the agent.
2. Agent queries the CRM for `status=unqualified` leads.
3. Evaluates each lead on Budget, Authority, Need, Timeline (1–5 each).
4. Writes score and new status (hot / warm / cold) back to CRM.
5. Records a metric snapshot for the pipeline dashboard.

**Manifest**:
```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: lead-qualifier
  description: "Hourly lead scoring agent — BANT criteria, auto-updates CRM status."
  department: sales
spec:
  stack: forgeos
  execution_type: scheduled
  schedule: "0 * * * *"
  ownership: shared
  llm:
    chat_model: claude-haiku-4-5-20251001
    provider: anthropic
  tools:
    - platform__crm_search_leads
    - platform__crm_update_lead
    - company__record_metric
  guardrails:
    max_cost_usd_per_day: 2.00
    max_tool_calls_per_run: 50
  system_prompt: |
    You qualify CRM leads hourly using BANT (Budget, Authority, Need, Timeline).
    Score each dimension 1-5. Total >= 16 → hot, >= 10 → warm, < 10 → cold.
    Update the CRM and record metrics after each batch.
```

---

## Use Case 3 — Email Triage (Scheduled + HITL)

**Scenario**: A founder wants her inbox processed every 30 minutes during business hours — categorized by urgency and with draft replies ready, but nothing sent without her approval.

**How it works**:
1. Fires every 30 minutes, Mon–Fri, 8 AM–8 PM.
2. Fetches up to 20 unread Gmail messages via MCP.
3. Classifies each: URGENT / FOLLOW_UP / INFORMATIONAL / SPAM.
4. For URGENT messages: drafts a reply, then calls `company__request_approval` — the agent pauses and the founder sees the draft in the dashboard.
5. Applies Gmail labels, records metrics, publishes NOTIFICATION events.
6. Nothing is sent until the human approves.

**Manifest**:
```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: email-triage
  description: "Scheduled email triage — classifies Gmail, drafts replies, requires human approval before sending."
  department: productivity
  labels:
    service: gmail
spec:
  stack: forgeos
  execution_type: scheduled
  ownership: personal
  owner_id: founder
  schedule: "*/30 8-20 * * 1-5"
  llm:
    chat_model: claude-sonnet-4-6
    provider: anthropic
  tools:
    - mcp__google-workspace__search_gmail_messages
    - mcp__google-workspace__get_gmail_message_content
    - mcp__google-workspace__draft_gmail_message
    - mcp__google-workspace__modify_gmail_message_labels
    - company__request_approval
    - company__record_metric
    - company__publish_event
  system_prompt: |
    Every run: fetch unread emails, classify as URGENT/FOLLOW_UP/INFORMATIONAL/SPAM.
    For URGENT: draft a reply, then request human approval before sending.
    Never send directly. Label everything, record metrics, publish events for urgent items.
  metadata:
    max_emails_per_run: 20
```

---

## Use Case 4 — Continuous Sales Pipeline (Always-On)

**Scenario**: Sales team wants a 24/7 background agent running the full lead-to-close cycle — no cron gaps, no human needed to trigger it.

**How it works**:
1. Boots at platform startup and runs forever (3-minute sleep between iterations).
2. Each iteration:
   - Searches CRM for new/updated leads.
   - Scores against ICP criteria from the knowledge base.
   - Gates outreach for leads >$10K behind human approval.
   - Updates CRM status, creates follow-up activities.
   - Records pipeline metrics and broadcasts events for marketing/CS agents.

**Manifest**:
```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: full-stack-sales
  description: "Always-on sales agent — continuous lead-to-close cycle, 24/7."
  department: sales
spec:
  stack: forgeos
  execution_type: always_on
  ownership: shared
  llm:
    chat_model: claude-sonnet-4-6
    provider: anthropic
  tools:
    - platform__crm_search_leads
    - platform__crm_update_lead
    - platform__crm_create_activity
    - company__search_knowledge
    - company__request_approval
    - company__record_metric
    - company__publish_event
  metadata:
    loop_interval_seconds: 180
  system_prompt: |
    You run a continuous lead-to-close cycle every 3 minutes.
    Score leads, gate high-value (>$10K) outreach behind human approval,
    update CRM, create follow-ups, record metrics, broadcast events.
```

---

## Use Case 5 — Employee Onboarding (Event-Driven)

**Scenario**: HR wants a zero-config process where every new hire automatically receives role-specific resources the moment the HR system fires a `employee.joined` event.

**How it works**:
1. Agent registers listeners for `employee.joined` and `employee.role_assigned`.
2. Sits idle with zero compute cost until one of those events fires.
3. Extracts employee context from the event payload.
4. Searches knowledge base for role-specific onboarding checklists.
5. Sends welcome message with day-1 resources via `platform__send_message`.
6. Publishes `onboarding.started` event so IT provisioning and buddy-matching agents can act.
7. Records onboarding metrics.

**Manifest**:
```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: onboarding-assistant
  description: "Event-driven onboarding — activates on employee.joined, delivers role-specific resources."
  department: hr
spec:
  stack: crewai
  execution_type: event_driven
  event_triggers:
    - employee.joined
    - employee.role_assigned
  ownership: shared
  llm:
    chat_model: claude-sonnet-4-6
    provider: anthropic
  tools:
    - company__search_knowledge
    - platform__send_message
    - company__publish_event
    - company__record_metric
  system_prompt: |
    On employee.joined: extract name/role/department, look up onboarding materials,
    send welcome message, publish onboarding.started for downstream agents.
    On employee.role_assigned: send role-specific training materials.
  metadata:
    crewai_role: "Onboarding Coordinator"
    crewai_goal: "Ensure every new hire has a smooth, well-resourced first day"
```

**Test**:
```bash
curl -X POST http://localhost:5000/api/events \
  -H "Content-Type: application/json" \
  -d '{"event_type": "employee.joined", "payload": {"name": "Jane", "role": "Engineer", "department": "Engineering"}}'
```

---

## Use Case 6 — Autonomous Research Agent

**Scenario**: Product team needs a deep research report on a topic. Instead of a one-shot prompt, they want an agent that plans its own research, runs multiple search iterations, builds findings, and stops when done.

**How it works**:
1. Deployed once with a `goal` string.
2. Runs autonomously for up to `max_iterations` loops (5-second sleep between).
3. Phase 1 (Discovery): searches knowledge base from multiple angles.
4. Phase 2 (Analysis): identifies patterns, records findings with `company__add_decision`.
5. Phase 3 (Synthesis): writes a coherent summary.
6. Outputs `[GOAL_COMPLETE]` to stop the loop.

**Manifest**:
```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: market-researcher
  description: "Autonomous researcher — iteratively builds a market analysis report."
  department: product
spec:
  stack: forgeos
  execution_type: autonomous
  ownership: shared
  goal: "Research the top 5 competitors in B2B sales automation and write a 600-word analysis"
  llm:
    chat_model: claude-opus-4-7
    provider: anthropic
  tools:
    - company__search_knowledge
    - company__add_decision
    - company__get_dashboard
  metadata:
    max_iterations: 12
    loop_interval_seconds: 5
  system_prompt: |
    You are an autonomous research agent. Work in phases:
    1. Discovery (iterations 1-4): search from multiple angles, note findings.
    2. Analysis (iterations 5-8): synthesize patterns, record each finding.
    3. Write (iterations 9-11): produce the final report, save it.
    4. When done, output [GOAL_COMPLETE].
    Never repeat work between iterations.
```

---

## Use Case 7 — Multi-Agent Orchestration (A2A)

**Scenario**: A CEO-level agent receives high-level business questions, discovers which specialist agents are deployed, delegates sub-tasks in parallel, and returns a synthesized executive answer.

**How it works**:
1. Human (or another agent) invokes the supervisor with a high-level question.
2. Supervisor calls `agent__list_available` to discover deployed specialists.
3. Fires parallel async calls (`agent__async_call`) to multiple specialists simultaneously — neither sees the other's work.
4. Awaits all results with `agent__await`.
5. Synthesizes a structured executive summary.

**Supervisor Manifest**:
```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: ceo-supervisor
  description: "CEO-level orchestrator — delegates to specialists, synthesizes results."
  department: executive
  labels:
    tier: executive
spec:
  stack: forgeos
  execution_type: reflex
  ownership: shared
  llm:
    chat_model: claude-opus-4-7
    provider: anthropic
  tools:
    - agent__call
    - agent__async_call
    - agent__await
    - agent__list_available
    - company__get_dashboard
  system_prompt: |
    You are the CEO supervisor. For any analysis request:
    1. Check company__get_dashboard for current metrics.
    2. Use agent__list_available to find specialists.
    3. Delegate sub-tasks via agent__async_call (parallel, no anchoring bias).
    4. Collect all results with agent__await.
    5. Synthesize into an executive summary with actionable recommendations.
```

**Specialist Manifest (example)**:
```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: revenue-analyst
  description: "Specialist: revenue analysis and forecasting."
  department: finance
  labels:
    tier: specialist
spec:
  stack: forgeos
  execution_type: reflex
  ownership: shared
  llm:
    chat_model: claude-sonnet-4-6
    provider: anthropic
  capabilities:
    a2a:
      canBeCalledBy:
        - namespace: default
          roles: [manager, executive]
  tools:
    - company__get_dashboard
    - company__search_knowledge
    - company__record_metric
  system_prompt: |
    You are a revenue analysis specialist. When called, analyze the data
    and return a structured report with findings and recommendations.
```

---

## Use Case 8 — Compliance Audit (Scheduled + Escalation)

**Scenario**: Legal/ops runs a weekly automated audit that scans for policy violations and escalates findings to a human reviewer before marking them resolved.

**How it works**:
1. Fires every Monday at 06:00 UTC.
2. Pulls current operational metrics from the dashboard.
3. Queries the last 7 days of events for anomalies.
4. Cross-references compliance requirements from the knowledge base.
5. For each violation: calls `company__request_approval` with severity + remediation steps.
6. Records a compliance score metric (0–100).
7. Produces a structured audit report.

**Manifest**:
```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: compliance-auditor
  description: "Weekly compliance audit — scans events for violations, escalates for human review."
  department: legal
spec:
  stack: adk
  execution_type: scheduled
  ownership: shared
  schedule: "0 6 * * 1"
  llm:
    chat_model: claude-sonnet-4-6
    provider: anthropic
  tools:
    - company__search_knowledge
    - company__query_events
    - company__record_metric
    - company__request_approval
    - company__get_dashboard
  system_prompt: |
    Every Monday at 6 AM: pull metrics, query last 7 days of events,
    cross-reference compliance policies, escalate each violation for human approval,
    record audit score, and produce a structured report (Summary, Findings, Violations, Recommendations).
```

---

## Use Case 9 — Full AgentOS v2 (Namespaces + Governance)

**Scenario**: Enterprise team with strict data boundaries, multi-team isolation, budget caps, PII handling rules, and mandatory audit signatures.

**How it works**: Uses the `agentos/v1` manifest format to declare all governance primitives. The kernel's syscall pipeline enforces them at runtime — no application code needed.

**Manifest**:
```yaml
apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: pii-data-processor
  namespace: legal
  description: "Processes sensitive customer data with strict PII controls and HITL approval."
  department: legal
  labels:
    compliance: gdpr
    tier: worker
  annotations:
    signed-by: security-team
spec:
  stack: forgeos
  execution_type: event_driven
  event_triggers:
    - customer.data.requested
  ownership: shared
  llm:
    chat_model: claude-sonnet-4-6
    provider: anthropic
  tools:
    - company__search_knowledge
    - company__request_approval

  capabilities:
    tools:
      allowed:
        - company__search_knowledge
        - company__request_approval
      denied:
        - platform__file_write
        - company__publish_event
    a2a:
      canCall:
        - namespace: legal
          roles: [analyst]
      canBeCalledBy:
        - namespace: legal
          agents: [compliance-auditor]
      max_depth: 2

  boundaries:
    budgets:
      daily_usd: 5.00
      per_task_usd: 0.50
      max_tool_calls_per_run: 20
    data:
      allowed_namespaces: [legal, compliance]
      blocked_namespaces: [sales, marketing]
      pii_policy: redact

  governance:
    human_in_loop:
      - event: data.export
        approvers: [legal-lead, dpo]
        sla_hours: 4.0
    audit_level: full
    signing_required: true

  observability:
    trace: langfuse
    log_level: debug
    emit_metrics: true

  memory:
    blocks:
      - name: context
        type: rolling_window
        max_items: 10

  system_prompt: |
    You process customer data requests with strict compliance controls.
    Redact all PII before logging. Always request human approval before
    any data export. Document your reasoning for every decision.
```

---

## Use Case 10 — Parallel Debate / Adversarial Reasoning

**Scenario**: Product team wants higher-quality strategic decisions by forcing two agents to argue opposite sides of a question simultaneously, with a synthesizer reconciling them.

**How it works**:
1. Facilitator receives a debate topic via invoke.
2. Fires two async calls simultaneously — one for "pro", one for "con" — neither sees the other.
3. Awaits both results.
4. Synthesizes a balanced recommendation with tradeoffs acknowledged.

**Manifest**:
```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: debate-facilitator
  description: "Solicits opposing viewpoints from two agents in parallel, synthesizes a balanced conclusion."
  department: strategy
spec:
  stack: forgeos
  execution_type: reflex
  ownership: shared
  llm:
    chat_model: claude-opus-4-7
    provider: anthropic
  tools:
    - agent__async_call
    - agent__await
    - agent__call
    - agent__list_available
  system_prompt: |
    You facilitate structured debates. For any question:
    1. Use agent__async_call to ask two agents for opposing viewpoints simultaneously.
       Neither agent should see the other's argument (prevents anchoring bias).
    2. Use agent__await to collect both responses.
    3. Synthesize a balanced conclusion with: key points from each side,
       shared ground, and a recommended course of action with tradeoffs.
```

---

## Manifest Field Reference

```yaml
apiVersion: forgeos/v1 | agentos/v1      # v1 = flat, v2 = full governance
kind: Agent | AgentContract

metadata:
  name: string                            # kebab-case, 2-64 chars
  namespace: default                      # team/logical isolation (v2)
  description: string
  department: string
  labels: {}                              # k8s-style selectors
  annotations: {}                         # signatures, audit refs

spec:
  # Runtime
  stack: forgeos | crewai | adk | openclaw | sandbox
  execution_type: reflex | scheduled | always_on | event_driven | autonomous
  ownership: personal | shared | client
  owner_id: string                        # required when ownership: client

  # LLM routing (model prefix auto-detects provider)
  llm:
    chat_model: claude-* | gpt-* | gemini-*
    provider: anthropic | openai | google | vertex
    reasoning_model: string               # optional separate model

  # Lifecycle triggers
  schedule: "0 * * * *"                  # required for scheduled
  event_triggers: [event.name]           # required for event_driven
  goal: string                           # required for autonomous

  # Tools
  tools:
    - company__*                          # in-process company handlers
    - platform__*                         # platform tools (CRM, messaging)
    - mcp__server__tool                   # MCP server tools
    - agent__call                         # A2A tools

  system_prompt: |                        # inline or file reference
    ./prompts/agent.md

  # Budget and content controls
  guardrails:
    max_tokens_per_run: int
    max_cost_usd_per_day: float
    max_tool_calls_per_run: int
    content_filter: none | default | strict

  # Structured memory
  memory:
    blocks:
      - name: string
        type: persistent | rolling_window | shared | scratch
        max_chars: 2000

  # Observability
  observability:
    trace: none | langfuse | langsmith | datadog
    log_level: debug | info | warning | error
    emit_metrics: true

  # Arbitrary per-agent config (loop intervals, role metadata, etc.)
  metadata:
    loop_interval_seconds: 60
    max_iterations: 10

  # v2 / AgentOS kernel sections
  capabilities:
    tools:
      allowed: [...]
      denied: [...]
    a2a:
      canCall: [{namespace, agents, roles}]
      canBeCalledBy: [{namespace, agents, roles}]
      max_depth: 5

  boundaries:
    budgets:
      daily_usd: float
      per_task_usd: float
    data:
      allowed_namespaces: [...]
      pii_policy: allow | detect | mask | redact | block

  governance:
    human_in_loop:
      - event: string
        approvers: [string]
        sla_hours: 24.0
    audit_level: none | basic | full
    signing_required: false

  dependencies:
    agents:
      - namespace: default
        name: string
        optional: false
    mcp_servers: [server-name]
```

---

## Quick Deploy Cheatsheet

```bash
# Deploy an agent
forgeos deploy agent.yaml

# Invoke (reflex or ad-hoc)
forgeos invoke <name> "your prompt here"

# Check status
forgeos status <name>          # or: curl http://localhost:5000/api/agents/<name>/status

# View run history
curl http://localhost:5000/api/agents/<name>/history?limit=5

# Stop a running loop
forgeos stop <name>            # or: curl -X POST .../stop

# Undeploy
forgeos undeploy <name>

# Push a test event
curl -X POST http://localhost:5000/api/events \
  -H "Content-Type: application/json" \
  -d '{"event_type": "my.event", "payload": {}}'

# Platform health
forgeos health
```
