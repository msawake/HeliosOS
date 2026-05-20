# Defining Agents

This guide walks through a complete, working agent manifest end-to-end using **`examples/sre-gcp-auditor`** as the worked example. By the end you'll know how to wire every section of `agent.yaml` to real behavior at runtime.

For an exhaustive field reference (types, defaults, enum values), see [Agent Manifest Reference](../reference/agent-manifest.md). For where the agent process actually executes, see [Runtime & Deployment](runtime-and-deployment.md).

---

## The anatomy of an `AgentContract`

Every agent is declared by a single YAML file. The full schema is enforced by Pydantic at `src/forgeos_sdk/manifest.py` — invalid manifests are rejected before deploy.

```yaml
apiVersion: agentos/v1     # or forgeos/v1 (flat legacy)
kind: AgentContract        # or Agent
metadata: { ... }          # who is this agent
spec:                      # what it does
  runtime: { ... }         # which framework runs it
  lifecycle: { ... }       # when it runs
  llm: { ... }             # which model
  capabilities: { ... }    # what tools + peers it can call
  boundaries: { ... }      # cost + data limits
  governance: { ... }      # audit + approval
  dependencies: { ... }    # required peers and MCP servers
```

You deploy it with:

```bash
forgeos validate examples/sre-gcp-auditor/manifest.yaml
forgeos deploy   examples/sre-gcp-auditor/manifest.yaml
```

The CLI is `src/forgeos_sdk/cli.py`; it validates, then `ForgeOSClient.deploy()` (`src/forgeos_sdk/client.py`) POSTs the manifest to `/api/platform/agents`. The platform executor (`src/platform/executor.py`) then registers the agent and wires its lifecycle.

---

## Walkthrough: `sre-gcp-auditor`

The SRE GCP Auditor is a daily read-only audit of every GCP project in your org. It illustrates almost every interesting manifest feature.

### 1. `metadata` — identification

```yaml
metadata:
  name: sre-gcp-auditor
  namespace: ops
  labels:
    team: sre
    scope: org-wide
    framework: adk
  annotations:
    description: "Daily read-only audit of all GCP projects"
    owner: platform-engineering
```

- `name` + `namespace` form the agent's stable identity. Other agents address it as `ops/sre-gcp-auditor`.
- `labels` are queryable — `Registry.list(namespace="ops", labels={"team": "sre"})` returns this agent. See `src/platform/registry.py`.
- `annotations` are free-form, used by humans and tooling (e.g. signature refs).

> Naming rule: `[a-zA-Z][a-zA-Z0-9_-]{1,63}`. Reject early — `forgeos validate` will tell you exactly which character broke the regex.

### 2. `spec.runtime` — which framework runs it

```yaml
spec:
  runtime:
    framework: adk
    image: forgeos-sre-gcp-auditor:latest
```

`framework` picks the [stack adapter](../architecture/stack-adapters.md):

| Value      | Adapter |
|------------|---------|
| `forgeos`  | Native agentic loop (`stacks/forgeos/adapter.py`) |
| `crewai`   | CrewAI SDK (`stacks/crewai/adapter.py`) |
| `adk`      | Google ADK Runner (`stacks/adk/adapter.py`) |
| `openclaw` | HTTP gateway subprocess (`stacks/openclaw/adapter.py`) |
| `langgraph`| LangChain/LangGraph adapter |

The SRE auditor uses **ADK** — its `agent.py` instantiates a `google.adk.Agent` and wraps every tool with `runtime.check_tool()` so the ForgeOS kernel still gets the final say. See [Where does the agent run?](runtime-and-deployment.md) for what `image` means in practice.

### 3. `spec.lifecycle` — when it runs

```yaml
  lifecycle:
    type: scheduled
    schedule: "0 6 * * *"     # 6 AM UTC daily
    restart_policy: on_failure
```

Five lifecycle types exist:

| `type`          | When it runs |
|-----------------|--------------|
| `always_on`     | Long-lived loop, restarts on failure |
| `scheduled`     | Cron — needs `schedule` |
| `event_driven`  | Subscribes to events — needs `event_triggers` |
| `reflex`        | Fires only when invoked via the API |
| `autonomous`    | Pursues an open-ended `goal` |

For `scheduled` agents, the platform scheduler (`src/platform/scheduler.py`) reads the cron and fires the executor at the right time. See [Runtime & Deployment](runtime-and-deployment.md) for how the cron actually delivers the trigger (in-process APScheduler vs. external Cloud Scheduler).

### 4. `spec.llm` — model routing

```yaml
  llm:
    chat_model: gemini-2.0-flash
    provider: google
```

The platform's LLM router (`src/platform/llm_router.py`) dispatches based on the provider. Provider values: `anthropic`, `openai`, `google`, `vertex`, `openclaw`, `atlas`. `metadata.fallback_provider` adds auto-failover.

### 5. `spec.capabilities` — tool ACLs and A2A peers

This is where most of the safety lives.

```yaml
  capabilities:
    tools:
      allowed:
        - gcp.list_projects
        - gcp.list_cloud_run_services
        - gcp.list_firewall_rules
        # ... read-only tools only
      denied:
        - gcp.create_*
        - gcp.delete_*
        - gcp.update_*
        - gcp.set_*
        - bash.*
    a2a:
      canCall:
        - ops/oncall-router
        - notifications/slack-bot
      canBeCalledBy: []
      max_depth: 1
```

**Tool ACL semantics** (enforced in `src/platform/syscall.py` when `FORGEOS_SYSCALL_PIPELINE=1`, else in `src/core/hooks.py`):

- `allowed` supports wildcards (e.g. `mcp__filesystem__*`).
- `denied` takes precedence over `allowed`. The `gcp.create_*` line above means the agent literally cannot call any GCP write API — even if the underlying tool is registered, the kernel rejects the call before dispatch.
- The agent code in `examples/sre-gcp-auditor/agent.py` wraps each ADK tool with `await runtime.check_tool(name, args)`. The check returns `denied` long before `gcloud` is shelled out.

**A2A (agent-to-agent)** is enforced by `src/platform/a2a.py`:

- `canCall` lists peers this agent may invoke (`agent__call`, `agent__async_call`).
- `canBeCalledBy: []` means **no one can call the auditor** — it is purely outbound. Useful for security-sensitive agents.
- `max_depth` caps recursion across A2A hops to prevent runaway agent graphs.

### 6. `spec.boundaries` — cost and data limits

```yaml
  boundaries:
    budgets:
      daily_usd: 3.00
      per_task_usd: 0.30
    data:
      allowed_namespaces:
        - ops
        - security
      pii_policy: redact
```

- Budgets are decremented in real time by the kernel's budget manager. When `daily_usd` is exhausted the next tool call is rejected with `BUDGET_EXCEEDED`.
- `per_task_usd` caps a single invocation — for this agent, one audit per GCP project.
- `data.pii_policy` ∈ `{allow, detect, mask, redact, block}` controls what happens when PII is detected in tool output. `redact` is the safe default for org-wide auditors.

### 7. `spec.governance` — humans in the loop

```yaml
  governance:
    human_in_loop:
      required_for:
        - severity.critical
    policies:
      - read_only_gcp
    audit_level: full
```

- The agent calls `runtime.ask_human(event="severity.critical", ...)` when it finds a critical issue (e.g. a public-facing DB). The kernel pauses the process and pages whoever the platform's [A2H protocol](../protocols/a2h-spec.md) routes critical events to.
- `policies` references named policy bundles registered in the platform (`src/platform/kernel.py`). `read_only_gcp` here is a custom policy that rejects any tool call whose name matches a write verb.
- `audit_level: full` writes every decision to the hash-chained audit log (`src/platform/audit.py`).

### 8. `spec.dependencies` — required peers

```yaml
  dependencies:
    mcp_servers: []
    agents:
      - ops/oncall-router
```

Admission fails if `ops/oncall-router` is not deployed when this agent starts. Optional deps use `optional: true`.

---

## The corresponding agent code

The YAML is a declaration; `agent.py` is the implementation. They meet at the runtime SDK:

```python
# examples/sre-gcp-auditor/agent.py (simplified)
from forgeos_sdk.runtime import Runtime

runtime = Runtime.from_env()   # reads FORGEOS_API_URL, FORGEOS_AGENT_ID

async def governed_tool(name, args):
    decision = await runtime.check_tool(name, args)
    if decision.denied:
        return {"error": decision.reason}
    result = await ALL_TOOLS[name](**args)
    await runtime.audit("tool_call", name=name, args=args, result=result)
    return result
```

Every governance field in `manifest.yaml` is enforced through these runtime calls. The manifest is the *contract*; the runtime is the *enforcement*.

See [Runtime API Reference](../runtime-api-reference.md) for the full SDK surface (`check_tool`, `get_budget`, `call_agent`, `ask_human`, `save_checkpoint`, `emit_metric`, `log_audit`).

---

## Next steps

- [Agent Manifest Reference](../reference/agent-manifest.md) — every field, type, default.
- [Runtime & Deployment](runtime-and-deployment.md) — where the agent process actually runs.
- [A2A Protocol](../architecture/a2a-protocol.md) — how `canCall` / `canBeCalledBy` are enforced.
- [Tool Enforcement](../architecture/tool-enforcement.md) — the path a tool call takes through the kernel.
