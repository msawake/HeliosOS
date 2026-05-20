# Agent Manifest Reference

The canonical format for declaring a ForgeOS agent. Inspired by Kubernetes CRDs, Microsoft Declarative Agents, and Open Agent Spec.

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
  human_in_loop:
    - event: email.send
      approvers: [team-lead, owner]
      sla_hours: 4.0
  policies:
    - name: no-pii-egress
      ref: policies/pii-egress.rego
    - name: no-shell-tools
      ref: policies/no-shell.json
  audit_level: full          # none | basic | full
  signing_required: false
```

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
    human_in_loop:
      - event: email.send
        approvers: [team-lead]
        sla_hours: 4.0
    policies:
      - name: no-pii-egress
        ref: policies/pii-egress.rego
    audit_level: full

  dependencies:
    agents:
      - namespace: sales-team
        name: crm-sync
        optional: false

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

ForgeOS also supports declaring a *team* of agents in a single manifest. Teams share defaults (stack, LLM, guardrails) and an orchestration pattern.

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
