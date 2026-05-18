# ForgeOS Runtime API Reference

Complete reference for `from forgeos_sdk.runtime import runtime` — the agent-side interface to the ForgeOS kernel.

This API works from ANY agent on ANY platform (ADK, CrewAI, Claude SDK, OpenAI, ForgeOS native). When used in-process, calls go directly to the kernel (~0.1ms). When used remotely (Mode C), calls go via HTTP (~50ms).

---

## Setup

```python
from forgeos_sdk.runtime import runtime
```

The runtime is a **module-level singleton**. It's automatically wired by ForgeOS at boot (`bootstrap.py:275`). In remote mode (Mode C), connect manually:

```python
from forgeos_sdk.kernel import Kernel
kernel = Kernel.connect()  # auto-detects local or remote
runtime.register_platform(kernel=kernel)
runtime.bind("my-agent-id", namespace="sales")
```

---

## Identity Properties

| Property | Type | Description | When set |
|----------|------|-------------|----------|
| `runtime.agent_id` | `str` | Current agent's ID (e.g., `"3cd5d08f-5f4"`) | After `bind()` |
| `runtime.namespace` | `str` | Current namespace (e.g., `"sales"`) | After `bind()` |
| `runtime.is_bound` | `bool` | Whether an agent identity is active | After `bind()` |
| `runtime.is_registered` | `bool` | Whether the kernel is wired | After `register_platform()` |

```python
# Example
print(f"I am {runtime.agent_id} in namespace {runtime.namespace}")
# → "I am 3cd5d08f-5f4 in namespace sales"
```

---

## Governance Checks

### `runtime.check_tool(tool_name, tool_input, estimated_cost_usd)` → `KernelDecision`

Check if the agent is allowed to call a tool. This is what ForgeOS calls automatically before every tool — but you can also call it explicitly for custom actions.

```python
decision = await runtime.check_tool("approve_discount", {"value": 500})
if decision.denied:
    print(f"Blocked: {decision.reason}")
    # → "Blocked: Tool 'approve_discount' not in agent's allowed tools"
elif decision.allowed:
    # proceed
```

**What the kernel checks (in order):**
1. `PermissionManager` — is `tool_name` in the agent's `capabilities.tools.allowed`?
2. `BudgetManager` — has the agent exceeded `boundaries.budgets.daily_usd`?
3. `PolicyEngine` — do any declarative policies deny this action?

**KernelDecision fields:**
| Field | Type | Values |
|-------|------|--------|
| `action` | `str` | `"allow"`, `"deny"`, `"mask"`, `"ask_human"`, `"rate_limit"` |
| `allowed` | `bool` | True if action == "allow" |
| `denied` | `bool` | True if action == "deny" |
| `needs_human` | `bool` | True if action == "ask_human" |
| `reason` | `str` | Human-readable explanation |
| `details` | `dict` | Structured data (e.g., `{"tool": "approve_discount", "allowed": [...]}`) |
| `audit_id` | `str` | Unique ID for this decision in the audit log |
| `timestamp` | `str` | ISO timestamp |

---

### `runtime.check_a2a(target_namespace, target_name)` → `KernelDecision`

Check if the agent can call another agent via A2A.

```python
decision = await runtime.check_a2a("finance", "budget-checker")
if decision.denied:
    print(f"Cannot call finance/budget-checker: {decision.reason}")
```

**What the kernel checks:**
1. Caller's `capabilities.a2a.canCall` — is the target in the list?
2. Callee's `capabilities.a2a.canBeCalledBy` — is the caller in the list?
3. Delegation depth — max chain depth not exceeded?

---

### `runtime.check_data(target_namespace)` → `KernelDecision`

Check if the agent can access data in another namespace.

```python
decision = await runtime.check_data("finance")
if decision.denied:
    print("Cannot access finance data")
    # Agent in "sales" namespace blocked from "finance" data
```

**What the kernel checks:**
- `boundaries.data.allowed_namespaces` — is `target_namespace` in the list?
- PII policy — should data be masked?

---

### `runtime.syscall(verb, target, args, dispatcher)` → `KernelDecision`

Run an operation through the **full syscall pipeline** (identity → capability → quota → policy → boundary → dispatch → audit). This is the most powerful check — it runs all stages.

```python
decision = await runtime.syscall(
    verb="tool.call",
    target="approve_discount",
    args={"value": 500, "estimated_cost_usd": 0.01},
)
```

**Available verbs:** `tool.call`, `a2a.invoke`, `data.read`, `secret.get`, `process.spawn`, `memory.write`

---

## Budget Management

### `runtime.budget()` → `BudgetSnapshot`

Get the agent's current budget state — how much it has spent, how much remains.

```python
budget = await runtime.budget()
print(f"Spent: ${budget.spent_today_usd:.2f} / ${budget.daily_limit_usd}")
print(f"Remaining: ${budget.remaining_usd:.2f}")
if budget.remaining_usd < 0.50:
    print("Warning: running low on budget")
```

**BudgetSnapshot fields:**
| Field | Type | Description |
|-------|------|-------------|
| `daily_limit_usd` | `float | None` | From manifest `boundaries.budgets.daily_usd` |
| `per_task_limit_usd` | `float | None` | From manifest `boundaries.budgets.per_task_usd` |
| `spent_today_usd` | `float` | Cumulative spend today |
| `reserved_usd` | `float` | Amount reserved by pending operations |
| `remaining_usd` | `float | None` | `daily_limit - spent - reserved` |

---

### `runtime.reserve(estimated_cost_usd, estimated_tokens)` → `str | None`

Reserve budget before an expensive operation. Returns a ticket ID or `None` if denied.

```python
ticket = await runtime.reserve(estimated_cost_usd=2.00)
if ticket is None:
    print("Budget reservation denied")
else:
    # Do the expensive work...
    await runtime.commit(ticket, actual_cost_usd=1.80)
```

### `runtime.commit(ticket, actual_cost_usd, actual_tokens)` → `KernelDecision`

Finalize a reservation with the actual cost (may differ from estimate).

### `runtime.release(ticket)` → `KernelDecision`

Release an unused reservation (operation was cancelled).

---

## Checkpoints

### `runtime.checkpoint(state)` → `None`

Save a durable checkpoint. The agent can resume from this point after a crash or restart.

```python
await runtime.checkpoint({
    "step": 3,
    "leads_processed": 45,
    "last_lead_id": "lead-089",
})
```

### `runtime.last_checkpoint()` → `CheckpointData | None`

Load the most recent checkpoint.

```python
cp = await runtime.last_checkpoint()
if cp:
    print(f"Resuming from step {cp.step_index}, crash count: {cp.crash_count}")
    state = cp.extra  # {"step": 3, "leads_processed": 45, ...}
```

**CheckpointData fields:**
| Field | Type | Description |
|-------|------|-------------|
| `pid` | `str` | Agent process ID |
| `generation` | `int` | Spec generation (increments on config change) |
| `phase` | `str` | Phase at checkpoint time |
| `step_index` | `int` | Loop iteration number |
| `crash_count` | `int` | How many times the agent has crashed |
| `goal` | `str | None` | For autonomous agents |
| `last_output_summary` | `str | None` | Summary of last output |
| `extra` | `dict` | Your custom state data |
| `created_at` | `str` | When the checkpoint was saved |

---

## Capabilities (Runtime Access Grants)

### `runtime.request_capability(target, verb, ttl, metadata)` → `CapabilityToken`

Request a time-limited access token for a specific resource.

```python
# Grant this agent access to finance/budget-checker for 10 minutes
token = await runtime.request_capability(
    target="finance/budget-checker",
    verb="a2a.invoke",
    ttl=600,  # seconds
    metadata={"reason": "quarterly review", "approved_by": "cfo-agent"},
)
print(f"Token: {token.id}, expires: {token.expires_at}")
```

### `runtime.revoke_capability(token_id)` → `bool`

Revoke a previously issued token.

### `runtime.list_capabilities()` → `list[CapabilityToken]`

List all active tokens issued to this agent.

**CapabilityToken fields:**
| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Opaque token ID (128-bit hex) |
| `subject` | `str` | PID the token was issued to |
| `target` | `str` | What the token authorizes access to |
| `verb` | `str` | Operation allowed (`"*"` = any) |
| `issued_at` | `str` | ISO timestamp |
| `expires_at` | `str | None` | Expiry (None = no expiry) |
| `metadata` | `dict` | Context (reason, issuer, etc.) |

---

## Signals

### `runtime.pending_signals()` → `list[str]`

Check for signals sent to this agent (e.g., `parent_terminated`, `budget_exceeded`).

```python
signals = await runtime.pending_signals()
if "parent_terminated" in signals:
    print("Parent died — draining gracefully")
    await runtime.checkpoint({"drain_reason": "parent_terminated"})
    return  # stop processing
```

### `runtime.signal(target_pid, signal_name, reason)` → `bool`

Send a signal to another agent process.

```python
await runtime.signal("worker-3", "pause", reason="maintenance window")
```

**Standard signals:** `parent_terminated`, `parent_quarantined`, `budget_exceeded`, `pause`, `resume`, `drain`

---

## Process & Contract Introspection

### `runtime.process()` → `ProcessSnapshot | None`

Get the agent's own process state from the process table.

```python
proc = await runtime.process()
print(f"Phase: {proc.phase}")
print(f"Tokens used: {proc.tokens_in + proc.tokens_out}")
print(f"Cost: ${proc.dollars:.4f}")
print(f"Tool calls: {proc.tool_calls}")
print(f"Signals: {proc.pending_signals}")
```

**ProcessSnapshot fields:**
| Field | Type | Description |
|-------|------|-------------|
| `pid` | `str` | Process ID |
| `name` | `str` | Agent name |
| `namespace` | `str` | Namespace |
| `phase` | `str` | `pending`, `admitted`, `starting`, `running`, `draining`, `stopped`, `failed`, `quarantined`, `evicted` |
| `tokens_in` | `int` | Cumulative input tokens |
| `tokens_out` | `int` | Cumulative output tokens |
| `dollars` | `float` | Cumulative cost |
| `tool_calls` | `int` | Total tool calls |
| `wallclock_ms` | `float` | Total execution time |
| `pending_signals` | `list[str]` | Undelivered signals |
| `generation` | `int` | Config generation |

### `runtime.contract()` → `dict | None`

Get the agent's full contract (the manifest as deployed).

```python
contract = await runtime.contract()
tools = contract.get("tools", [])
budget = contract.get("metadata", {}).get("_boundaries", {}).get("budgets", {})
print(f"Allowed tools: {tools}")
print(f"Daily budget: ${budget.get('daily_usd', 'unlimited')}")
```

---

## Agent-to-Human (A2H)

### `runtime.ask_human(namespace, name, question, ...)` → `dict`

Ask a human a structured question. The request enters the HITL approval queue.

```python
result = await runtime.ask_human(
    namespace="sales",
    name="manager",
    question="Should we offer a 25% discount to Acme Corp?",
    response_type="choice",
    options=[
        {"label": "Approve", "value": "approve"},
        {"label": "Deny", "value": "deny"},
        {"label": "Counter-offer 15%", "value": "counter_15"},
    ],
    priority="high",
    deadline="2026-05-18T00:00:00Z",
)
print(f"Request ID: {result['id']}, Status: {result['status']}")
```

### `runtime.notify_human(namespace, name, message, ...)` → `dict`

Send a notification (no response needed).

```python
await runtime.notify_human(
    namespace="ops",
    name="oncall",
    message="Agent detected 3 failed campaigns — auto-quarantined",
    priority="high",
)
```

---

## Audit

### `runtime.audit(event, details)` → `None`

Record a custom audit event in the hash-chained audit log.

```python
await runtime.audit("discount.approved", {
    "customer": "acme-corp",
    "discount_percent": 15,
    "approved_by": "sales-manager",
    "deal_value": 50000,
})
```

The event is recorded with:
- `agent_id` — automatically from the bound identity
- `timestamp` — automatically set
- `prev_hash` — hash chain ensures tamper-proof ordering
- Your custom `event` name and `details` dict

---

## How It Works Across Platforms

### In-Process (ForgeOS, ADK, CrewAI when running inside ForgeOS)

```python
runtime.check_tool("approve_discount", {"value": 500})
# → Direct Python call to kernel.check_tool_call()
# → ~0.1ms
# → Returns KernelDecision from in-memory kernel
```

### Remote HTTP (Mode C — agent on separate Cloud Run)

```python
runtime.check_tool("approve_discount", {"value": 500})
# → HTTP POST https://forgeos-api-.../api/platform/kernel/check-tool
# → ~50ms (network round-trip)
# → Returns KernelDecision parsed from JSON response
```

The runtime auto-detects which path to use based on how `register_platform()` was called:
- `Kernel.local()` → in-process (`_InProcessBackend`)
- `Kernel.remote(url)` → HTTP (`_HTTPBackend`)
- `Kernel.connect()` → tries local first, falls back to HTTP

---

## Complete Method Index

| Category | Method | Returns | Auto-called by ForgeOS? |
|----------|--------|---------|------------------------|
| **Identity** | `runtime.agent_id` | `str` | Yes (bind at invoke) |
| **Identity** | `runtime.namespace` | `str` | Yes (bind at invoke) |
| **Identity** | `runtime.is_bound` | `bool` | — |
| **Identity** | `runtime.is_registered` | `bool` | — |
| **Setup** | `runtime.register_platform(kernel, ...)` | `None` | Yes (at boot) |
| **Setup** | `runtime.bind(agent_id, namespace)` | `Token` | Yes (at invoke) |
| **Setup** | `runtime.unbind(token)` | `None` | Yes (after invoke) |
| **Governance** | `runtime.check_tool(name, input, cost)` | `KernelDecision` | Yes (before every tool) |
| **Governance** | `runtime.check_a2a(namespace, name)` | `KernelDecision` | Yes (before A2A calls) |
| **Governance** | `runtime.check_data(namespace)` | `KernelDecision` | No — explicit only |
| **Governance** | `runtime.syscall(verb, target, args)` | `KernelDecision` | No — explicit only |
| **Budget** | `runtime.budget()` | `BudgetSnapshot` | No — explicit only |
| **Budget** | `runtime.reserve(cost)` | `str | None` | No — explicit only |
| **Budget** | `runtime.commit(ticket, cost)` | `KernelDecision` | No — explicit only |
| **Budget** | `runtime.release(ticket)` | `KernelDecision` | No — explicit only |
| **Checkpoint** | `runtime.checkpoint(state)` | `None` | No — explicit only |
| **Checkpoint** | `runtime.last_checkpoint()` | `CheckpointData | None` | No — explicit only |
| **Capabilities** | `runtime.request_capability(target, verb, ttl)` | `CapabilityToken` | No — explicit only |
| **Capabilities** | `runtime.revoke_capability(token_id)` | `bool` | No — explicit only |
| **Capabilities** | `runtime.list_capabilities()` | `list[CapabilityToken]` | No — explicit only |
| **Signals** | `runtime.pending_signals()` | `list[str]` | No — explicit only |
| **Signals** | `runtime.signal(pid, name, reason)` | `bool` | No — explicit only |
| **Introspection** | `runtime.process()` | `ProcessSnapshot | None` | No — explicit only |
| **Introspection** | `runtime.contract()` | `dict | None` | No — explicit only |
| **A2H** | `runtime.ask_human(namespace, name, question, ...)` | `dict` | No — explicit only |
| **A2H** | `runtime.notify_human(namespace, name, message, ...)` | `dict` | No — explicit only |
| **Audit** | `runtime.audit(event, details)` | `None` | No — explicit only |

**"Auto-called by ForgeOS?"** = ForgeOS calls this automatically. The rest are available for agent developers who want deeper governance integration.

---

## Platform Interception Summary

ForgeOS automatically calls these runtime methods through each platform's native extension point:

| Platform | Extension Point | ForgeOS Auto-Calls | Agent Code Changes Needed |
|----------|----------------|-------------------|--------------------------|
| **ForgeOS native** | `_execute_tool()` in agentic loop | `bind()`, `check_tool()`, `unbind()` | 0 lines |
| **Google ADK** | `FunctionTool(async wrapper)` | `bind()`, `check_tool()`, `unbind()` | 0 lines |
| **CrewAI** | `class ForgeOSTool(BaseTool)._run()` | `bind()`, `check_tool()`, `unbind()` | 0 lines |
| **Claude Agent SDK** | `PreToolUse` hook | `bind()`, `check_tool()`, `unbind()` | 0 lines |
| **OpenAI Agents SDK** | `AgentHooks.on_tool_start()` | `bind()`, `check_tool()`, `unbind()` | 0 lines |

**Additional methods available for explicit use** (any platform, any number of lines):
- `check_data()`, `check_a2a()` — gate data access and delegation
- `budget()`, `reserve()`, `commit()` — fine-grained budget control
- `checkpoint()`, `last_checkpoint()` — durable state
- `request_capability()` — time-limited access tokens
- `pending_signals()` — check for parent death, budget exceeded
- `ask_human()`, `notify_human()` — human-in-the-loop
- `audit()` — custom audit entries
- `process()`, `contract()` — self-introspection
