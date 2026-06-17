# Agent Manifest Reference

The canonical format for declaring a Helios OS agent. Inspired by Kubernetes CRDs, Microsoft Declarative Agents, and Open Agent Spec.

## Top-Level Structure

```yaml
apiVersion: agentos/v1           # or forgeos/v1 for legacy flat format
kind: AgentContract              # or Agent
metadata: { ... }                # identification
spec: { ... }                    # runtime specification
status: { ... }                  # runtime status (controller-filled, read-only)
```

Both `apiVersion` values validate through the same Pydantic schema.

## `metadata` (identification)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | yes | — | Agent name; `[a-zA-Z][a-zA-Z0-9_-]{1,63}` |
| `namespace` | string | no | `default` | Logical isolation group (like k8s namespaces) |
| `uid` | string | no | auto | Stable agent ID |
| `version` | string | no | `1.0.0` | Semver version of this spec |
| `generation` | int | no | `1` | Increments on every spec change (set by controller) |
| `description` | string | no | `""` | Human-readable description |
| `department` | string | no | `""` | Organizational grouping |
| `labels` | dict | no | `{}` | k8s-style labels for selection (`{domain: sales, tier: prod}`) |
| `annotations` | dict | no | `{}` | Non-identifying metadata (signatures, audit refs) |

## `spec` (runtime specification)

### `spec.runtime` (v2 — which framework runs this agent)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `framework` | enum | `forgeos` | `forgeos`, `crewai`, `adk`, `openclaw`, `langgraph` |
| `image` | string | — | Versioned artifact reference (e.g. `sales-agent:2.1.0`) |
| `image_pull_policy` | enum | `IfNotPresent` | `Always`, `IfNotPresent`, `Never` |

If `spec.runtime` is omitted, falls back to the flat `spec.stack` field.

### `spec.lifecycle` (v2 — execution pattern)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | enum | `reflex` | `always_on`, `scheduled`, `event_driven`, `reflex`, `autonomous` |
| `replicas` | int | `1` | Desired number of running instances |
| `restart_policy` | enum | `OnFailure` | `Always`, `OnFailure`, `Never` |
| `schedule` | string | — | Cron expression (for `scheduled`) |
| `event_triggers` | list[string] | `[]` | Event names to subscribe to (for `event_driven`) |
| `goal` | string | `""` | Goal description (for `autonomous`) |

If `spec.lifecycle` is omitted, falls back to the flat `execution_type`, `schedule`, `event_triggers`, `goal` fields.

### `spec.llm` (required — LLM routing)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `chat_model` | string | — | `claude-sonnet-4-5-20250514`, `gpt-4o`, etc. |
| `reasoning_model` | string | — | Optional separate model for reasoning |
| `provider` | enum | `anthropic` | `anthropic`, `openai`, `google`, `vertex`, `openclaw`, `atlas` |
| `metadata.fallback_provider` | string | — | Auto-failover target on primary failure |

### `spec.capabilities` (v2 — tool ACLs + A2A)

```yaml
capabilities:
  tools:
    allowed:
      - mcp__filesystem__*
      - company__publish_event
    denied:
      - shell.exec
      - mcp__filesystem__delete
  a2a:
    canCall:
      - namespace: sales-team
        agents: [cfo, researcher]
    canBeCalledBy:
      - namespace: marketing
        roles: [director]
    max_depth: 5
```

- Wildcards supported in `allowed` (e.g. `mcp__filesystem__*`)
- `denied` takes precedence over `allowed`
- A2A ACLs enforced by kernel at tool-call time
- If `spec.capabilities` is omitted, falls back to flat `spec.tools` list

### `spec.boundaries` (v2 — resource limits)

```yaml
boundaries:
  budgets:
    daily_usd: 50.00
    per_task_usd: 5.00
    max_tokens_per_run: 50000
    max_tool_calls_per_run: 20
    max_concurrent_tasks: 1
  data:
    allowed_namespaces: [sales, public]
    blocked_namespaces: [finance-pii]
    pii_policy: redact      # allow | detect | mask | redact | block
```

### `spec.triggers` (v2 — unified trigger list)

Instead of flat `schedule` + `event_triggers`, a rich list:

```yaml
triggers:
  - name: daily-report
    cron: "0 9 * * *"
  - name: new-lead
    webhook: /webhooks/lead-created
  - name: urgent-email
    event: email.incoming
    filter: "subject contains 'urgent'"
```

### `spec.system_prompt` (flexible)

Three forms supported:

**String (inline):**
```yaml
system_prompt: "You are a helpful assistant."
```

**Object (file + templating):**
```yaml
system_prompt:
  file: ./prompts/email.md
  variables:
    user_name: jama
    timezone: CET
  template_engine: jinja2
```

**Omitted** — defaults to `f"You are {name}. {description}"`.

### `spec.memory` (structured memory)

```yaml
memory:
  blocks:
    - name: user_preferences
      type: persistent
      max_chars: 2000
      update_policy: on_demand
    - name: recent_emails
      type: rolling_window
      max_items: 50
    - name: company_facts
      type: shared
      source: knowledge_base
```

Memory block types: `persistent`, `rolling_window`, `shared`, `scratch`.

**Memory mounts (v2)** — for sharing a memory store across agents in a namespace, similar to k8s volume mounts:

```yaml
memory:
  mounts:
    - name: team-knowledge
      scope: read-write      # read-only | read-write
      source: namespaces/sales/knowledge-base
      max_size_kb: 4096
      description: "Shared sales knowledge base"
  dreaming: false            # enable background memory consolidation
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | — | Mount handle used by the agent at runtime |
| `scope` | enum | `read-write` | `read-only` or `read-write` |
| `source` | string | — | URI or namespace path of the backing store |
| `max_size_kb` | int | — | Hard cap on the mount size |
| `description` | string | `""` | Human-readable description |

### `spec.governance` (audit + approval + policies)

```yaml
governance:
  approvals:                          # per-tool, kernel-enforced human approval
    - tool: notify__email             # exact name or wildcard prefix (e.g. notify__*)
      mode: always                    # always | never | conditional
      approvers: [ceo-office]         # roles or user names who may approve
      sla_hours: 4.0                  # deadline before on_timeout fires
      priority: high                  # low | medium | high | critical
      on_timeout: abort               # proceed | abort | reask
      reason: "External comms require sign-off"
    - tool: notify__email             # conditional: only fire for external recipients
      mode: conditional
      when:
        ask_human_if: {op: not_endswith_any, field: tool_input.to, value: ["@acme.com"]}
    - tool: payments__charge          # conditional: only above a threshold
      mode: conditional
      when:
        ask_human_if: {op: gt, field: tool_input.amount_usd, value: 500}
  policies:
    - name: no-pii-egress             # external policy file (OPA/Rego or JSON-logic)
      ref: policies/pii-egress.rego
    - name: external-email-guard      # inline JSON-logic (deny_if / ask_human_if; deny wins)
      inline:
        ask_human_if: {op: not_endswith_any, field: tool_input.to, value: ["@acme.com"]}
  audit_level: full                   # none | basic | full
  signing_required: false
```

**`approvals`** is the modern, kernel-enforced approval mechanism. The kernel
matches every tool call against these rules; when one fires it returns
`ask_human` instead of executing — the runtime then suspends the agent
**durably** and resumes once a human approves, running the gated tool with a
capability token. The agent never calls an approval function itself; it makes
the normal tool call and the kernel intercepts.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `tool` | string | — (required) | Tool name or wildcard prefix (`notify__*`) |
| `mode` | enum | `always` | `always`, `never`, `conditional` |
| `when` | object | — | JSON-logic clause; **required** when `mode: conditional` (e.g. `{ask_human_if: {op, field, value}}`) |
| `approvers` | list | `[]` | Roles or user names who may approve |
| `sla_hours` | float | `24.0` | Deadline before `on_timeout` fires |
| `priority` | enum | `medium` | `low`, `medium`, `high`, `critical` |
| `on_timeout` | enum | `abort` | `proceed`, `abort`, `reask` |
| `reason` | string | `""` | Rationale shown to the approver |

`policies` entries take **either** `ref` (a `.rego`/`.json` file) **or**
`inline` JSON-logic. Inline rules are tri-state: a `deny_if` clause denies the
action, an `ask_human_if` clause routes it through approval (if both appear,
deny wins).

!!! note "`human_in_loop` is deprecated"
    The older `human_in_loop` list (keyed by `event:`) still validates but is
    **automatically folded into `approvals`** — each entry becomes an `always`
    rule whose `tool` is the legacy `event`. Prefer `approvals` for new
    manifests.

### `spec.dependencies` (systemd-like ordering)

```yaml
dependencies:
  agents:
    - namespace: sales-team
      name: crm-sync
      optional: false
      min_version: "2.0.0"
  mcp_servers: [filesystem, google-workspace]
```

Admission fails if non-optional dependencies are missing.

### `spec.scope` (organizational taxonomy)

Where the agent sits in the company. Used by the kernel to resolve hierarchical
policies (Global > Namespace > Agent) and by RAG pipelines to filter knowledge
by department/team/role. All fields are optional strings (default `""`).

```yaml
scope:
  department: finance          # finance, hr, sales, engineering, ...
  team: treasury               # team within the department
  role: treasury-analyst       # job role this agent serves
  job_id: TRS-001              # internal job code
```

### `spec.knowledge` (RAG / knowledge scoping)

What data the agent can see — RAG retrieval filters plus the knowledge sources
it may access.

```yaml
knowledge:
  rag_filter:                  # metadata filter applied to every RAG query
    department: finance
    team: treasury
  allowed_sources:             # paths the agent may read
    - knowledge/departments/finance/
  blocked_sources:             # paths explicitly denied
    - knowledge/departments/hr/
  sources:                     # typed source declarations
    - path: knowledge/departments/finance/
      type: markdown           # markdown | rag | google_sheet | google_doc | database | api
      description: "Finance SOPs and treasury playbooks"
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `rag_filter` | object | `{}` | Metadata filter applied to RAG queries |
| `allowed_sources` | list | `[]` | Paths the agent may read |
| `blocked_sources` | list | `[]` | Paths explicitly denied |
| `sources` | list | `[]` | Typed `{path, type, description}` declarations |

### `spec.ownership`

| Value | Meaning | owner_id required |
|-------|---------|-------------------|
| `personal` | Owned by a single user | yes |
| `shared` | Company-wide | no |
| `client` | Client-scoped (isolated MCP servers) | yes (client_id) |

### `spec.guardrails` / `spec.observability` / `spec.metadata`

```yaml
guardrails:
  max_tokens_per_run: 10000
  max_cost_usd_per_day: 5.00
  max_tool_calls_per_run: 20
  content_filter: default
  allowed_models: [gpt-4o, claude-sonnet-4-5]

observability:
  trace: langfuse           # none | langfuse | langsmith | datadog
  log_level: info           # debug | info | warning | error
  emit_metrics: true

metadata:
  report_path: /data/inbox/daily-report.md
  inbox_path: /data/inbox/messages.json
```

`metadata` is arbitrary free-form config accessible to the agent at runtime.

## `status` (runtime state — controller-filled)

Read-only from the manifest author's POV. Populated by the controller.

```yaml
status:
  phase: Running             # Pending | Running | Succeeded | Failed | Quarantined | Unknown
  conditions:
    - type: Ready
      status: "True"
      last_transition: "2026-04-16T10:00:00Z"
  current_activity: "Processing batch 3/5"
  last_run_at: "2026-04-16T09:58:00Z"
  runs_today: 42
  cost_today_usd: 12.50
  avg_latency_ms: 2300
  observed_generation: 5
```

## Complete Example

```yaml
apiVersion: agentos/v1
kind: AgentContract

metadata:
  name: sales-researcher
  namespace: sales-team
  version: "2.1.0"
  labels:
    domain: sales
    tier: production
  annotations:
    forgeos.io/signed-by: "cosign:sha256:abc..."

spec:
  runtime:
    framework: forgeos
    image: "sales-researcher:2.1.0"

  lifecycle:
    type: always_on
    replicas: 1
    restart_policy: OnFailure

  llm:
    chat_model: claude-sonnet-4-5-20250514
    provider: anthropic
    metadata:
      fallback_provider: openai

  capabilities:
    tools:
      allowed:
        - mcp__filesystem__*
        - company__publish_event
        - company__search_knowledge
      denied:
        - shell.exec
    a2a:
      canCall:
        - namespace: sales-team
          agents: [cfo]
      canBeCalledBy:
        - namespace: marketing
          agents: [director]
      max_depth: 3

  boundaries:
    budgets:
      daily_usd: 45.00
      per_task_usd: 5.00
      max_tokens_per_run: 30000
    data:
      allowed_namespaces: [sales, public]
      blocked_namespaces: [finance-pii, hr]
      pii_policy: redact

  triggers:
    - cron: "0 7 * * *"
    - webhook: /webhooks/lead-created
    - event: email.incoming
      filter: "subject contains 'sales'"

  memory:
    blocks:
      - name: research_cache
        type: rolling_window
        max_items: 100
      - name: user_preferences
        type: persistent
        max_chars: 1500

  governance:
    approvals:
      - tool: notify__email
        mode: conditional
        approvers: [team-lead]
        sla_hours: 4.0
        when:
          ask_human_if: {op: not_endswith_any, field: tool_input.to, value: ["@acme.com"]}
    policies:
      - name: no-pii-egress
        ref: policies/pii-egress.rego
    audit_level: full

  dependencies:
    agents:
      - namespace: sales-team
        name: crm-sync
        optional: false

  scope:
    department: sales
    team: pipeline
    role: sales-researcher

  knowledge:
    rag_filter:
      department: sales
    allowed_sources:
      - knowledge/departments/sales/
    sources:
      - path: knowledge/departments/sales/
        type: markdown
        description: "Sales playbooks and ICP definitions"

  system_prompt:
    file: ./prompts/sales-researcher.md
    variables:
      company_name: Acme Corp
    template_engine: jinja2
```

## Validation

The SDK validates manifests via Pydantic models. Use `forgeos validate ./agent.yaml` to check before deploying.

Validation errors returned with exact field paths:

```
$ forgeos validate bad.yaml
✗ Validation failed: 1 validation error for AgentManifest
  spec.lifecycle.type
    Input should be 'always_on', 'scheduled', 'event_driven', 'reflex' or 'autonomous'
    [type=literal_error, input_value='always-on', input_type=str]
```

## Team manifests (`kind: Team`)

Helios OS also supports declaring a *team* of agents in a single manifest. Teams share defaults (stack, LLM, guardrails) and an orchestration pattern.

```yaml
apiVersion: agentos/v1
kind: Team
metadata:
  name: sales-pod
  namespace: sales-team
spec:
  orchestration: supervisor   # supervisor | parallel | sequential | mesh
  defaults:
    llm: { chat_model: claude-sonnet-4-5-20250514, provider: anthropic }
  agents:
    - name: lead-supervisor
      role: supervisor
      system_prompt: "You orchestrate the sales pod."
    - name: researcher
      role: worker
    - name: writer
      role: specialist
  shared_context:
    namespace: sales-team/shared
    tools: [company__search_knowledge]
```

Team roles: `supervisor`, `worker`, `specialist`, `curator`. Orchestration modes: `supervisor`, `parallel`, `sequential`, `mesh`.

Team deployment is handled by `Executor.deploy_team()` (`src/platform/executor.py`), which builds individual agents and wires the A2A graph between them automatically. See `src/forgeos_sdk/manifest.py` (`TeamManifest`) for the full schema.

## Backward Compatibility

Flat v1 manifests continue to work — the SDK resolves v2 fields to v1 wire format on deploy:

```yaml
# v1 — still valid
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: simple-agent
spec:
  stack: forgeos
  execution_type: reflex
  llm:
    chat_model: gpt-4o
    provider: openai
  tools: [mcp__filesystem__*]
```
