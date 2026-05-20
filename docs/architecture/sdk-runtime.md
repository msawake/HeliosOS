# SDK Runtime

The agent-side interface to the ForgeOS kernel. Every agent gets a `runtime` singleton that knows who it is and mediates all interactions with governance, budget, checkpoints, capabilities, and signals.

## Why It Exists

Before the runtime, agents had no way to interact with the kernel. Tool calls were checked by the platform, but the agent itself couldn't:

- Ask "am I allowed to do X?" before trying
- See how much budget it has left
- Save checkpoints at logical boundaries
- Request capability tokens for cross-namespace operations
- Handle shutdown signals gracefully

The runtime solves all five. It's the "libc" of ForgeOS — the standard library agents use to interact with their operating system.

## Architecture

```
Agent Code                    SDK Runtime                   Kernel
─────────────────────────────────────────────────────────────────────
from forgeos_sdk              runtime (singleton)            Kernel facade
  import runtime              ├── contextvars identity       ├── AdmissionController
                              ├── _kernel reference          ├── PermissionManager
runtime.check_tool("x")  ──► ├── _process_table ref    ──► ├── BudgetManager
runtime.budget()          ──► ├── _checkpoint_store ref ──► ├── PolicyEngine
runtime.checkpoint({...}) ──► └── bind/unbind per invoke    ├── DataBoundaryManager
                                                            └── CapabilityManager
```

## Two-Phase Injection

### Phase 1: Boot (once)

Bootstrap registers platform references so the runtime can reach the kernel:

```python
# src/bootstrap.py — after kernel is created
from src.forgeos_sdk.runtime import runtime as sdk_runtime
sdk_runtime.register_platform(
    kernel=self._kernel,
    process_table=self.executor.process_table,
    checkpoint_store=self.executor.checkpoint_store,
)
```

### Phase 2: Per-Invocation (every agent call)

The executor binds agent identity before delegating to the adapter:

```python
# src/platform/executor.py — inside invoke()
from src.forgeos_sdk.runtime import runtime as _sdk_runtime
_rt_token = _sdk_runtime.bind(agent_id, namespace=agent_def.namespace)
try:
    result = await adapter.invoke(agent_id, prompt, context)
finally:
    _sdk_runtime.unbind(_rt_token)
```

Identity is stored in a `contextvars.ContextVar`, making it async-safe — concurrent invocations in the same process each see their own agent context.

## Usage from Agent Code

```python
from forgeos_sdk import runtime

# Identity — automatic, set by executor
runtime.agent_id       # "a7ba147d-5fb"
runtime.namespace      # "sales"

# Policy checks — no agent_id needed
decision = await runtime.check_tool("email.send")
if decision.denied:
    print(f"Cannot send email: {decision.reason}")

# Budget introspection
budget = await runtime.budget()
print(f"${budget.remaining_usd:.2f} remaining of ${budget.daily_limit_usd}")

# Two-phase budget reservation
ticket = await runtime.reserve(0.05)       # hold $0.05
# ... do the work ...
await runtime.commit(ticket, 0.03)         # actual was $0.03
# or: await runtime.release(ticket)        # abort, give it back

# Checkpoints — save state at logical boundaries
await runtime.checkpoint({"step": 3, "leads_processed": 47})
# After crash:
restored = await runtime.last_checkpoint()
if restored:
    start_from = restored.extra["step"]

# Capability tokens — scoped runtime grants
cap = await runtime.request_capability(
    target="finance/cfo", verb="a2a.invoke", ttl=300
)
# ... use the capability ...
await runtime.revoke_capability(cap.id)

# Signals — cooperative preemption
signals = await runtime.pending_signals()
if "SIGTERM" in signals:
    await runtime.checkpoint(current_state)
    return  # graceful exit

# Contract + process introspection
contract = await runtime.contract()
process = await runtime.process()
print(f"Phase: {process.phase}, Tools: {process.tool_calls}")

# Audit — record custom events
await runtime.audit("decision_made", {"choice": "approved"})

# Syscall — full pipeline
decision = await runtime.syscall("tool.call", target="email.send")
```

## How It Works Across Adapters

The runtime is injected at the executor level (before any adapter code runs), so it works identically across all five adapters:

| Adapter | How tools hit the kernel |
|---------|------------------------|
| ForgeOS | `_execute_tool()` calls `runtime.check_tool()` before `tool_executor.execute()` |
| CrewAI | `BaseTool._run()` calls `runtime.check_tool()` in a sync event loop |
| ADK | `FunctionTool` async wrapper calls `runtime.check_tool()` before execution |
| OpenClaw | `ToolProxyServer` validates token + calls `runtime.check_tool()` via HTTP |
| Sandbox | `/api/sandbox/tool` endpoint validates token + checks kernel |

## Data Types

### BudgetSnapshot

```python
@dataclass
class BudgetSnapshot:
    daily_limit_usd: float | None    # None = unlimited
    per_task_limit_usd: float | None
    spent_today_usd: float
    reserved_usd: float              # outstanding reservations
    remaining_usd: float | None      # daily - spent - reserved
```

### ProcessSnapshot

```python
@dataclass
class ProcessSnapshot:
    pid: str
    name: str
    namespace: str
    phase: str          # pending, admitted, starting, running, draining, stopped, failed
    tokens_in: int
    tokens_out: int
    dollars: float
    tool_calls: int
    wallclock_ms: float
    pending_signals: list[str]
    generation: int
```

### CheckpointData

```python
@dataclass
class CheckpointData:
    pid: str
    generation: int
    phase: str
    step_index: int
    crash_count: int
    goal: str | None
    last_output_summary: str | None
    extra: dict         # your custom state goes here
    created_at: str
```

### CapabilityToken

```python
@dataclass
class CapabilityToken:
    id: str             # opaque 128-bit hex handle
    subject: str        # the agent this token was issued to
    target: str         # what it grants access to ("finance/cfo")
    verb: str           # the operation ("a2a.invoke", "*")
    issued_at: str
    expires_at: str | None
    metadata: dict
```

## Error Handling

- Methods before `register_platform()` raise `RuntimeError("Runtime not registered")`
- Methods before `bind()` raise `RuntimeError("Runtime not bound to an agent")`
- Kernel unavailability is caught gracefully — tools execute without checks (backward compatible)

## Source Files

- `src/forgeos_sdk/runtime.py` — Runtime class, data types, module singleton
- `src/forgeos_sdk/__init__.py` — Exports: `runtime`, `Runtime`, `BudgetSnapshot`, `ProcessSnapshot`, `CheckpointData`, `CapabilityToken`
- `src/platform/executor.py` — `bind()`/`unbind()` injection in `invoke()`
- `src/bootstrap.py` — `register_platform()` call
- `tests/test_sdk_runtime.py` — 31 tests
