# Process Table

The first-class runtime unit of scheduling and accounting. Every agent admitted by the kernel gets a process record with a stable PID, a unified phase machine, and resource accounting.

## AgentProcess

```python
@dataclass
class AgentProcess:
    identity: AgentIdentity     # stable ID, namespace, generation
    spec_ref: str               # links back to AgentDefinition
    phase: Phase                # current lifecycle phase
    resource_usage: ResourceUsage  # tokens, dollars, tool calls, wallclock
    pending_signals: list[str]  # cooperative preemption queue
    last_error: str | None
    created_at: str
    phase_changed_at: str
```

## AgentIdentity

```python
@dataclass
class AgentIdentity:
    pid: str                    # matches AgentDefinition.agent_id
    name: str                   # human-readable name
    namespace: str              # logical isolation ("sales", "finance")
    generation: int             # bumps on material spec change
    owner_id: str | None        # who deployed this agent
    tenant_id: str              # multi-tenant isolation
    parent_pid: str | None      # if spawned by another agent
```

## Phase Machine

```
PENDING → ADMITTED → STARTING → RUNNING → DRAINING → STOPPED
                                    ↓          ↓
                                  FAILED    EVICTED
                                    ↓
                               QUARANTINED
```

| Phase | Meaning |
|-------|---------|
| `PENDING` | Created but not yet admitted |
| `ADMITTED` | Passed admission control, ready to start |
| `STARTING` | Adapter is initializing the agent |
| `RUNNING` | Actively processing (accepting invocations) |
| `DRAINING` | Finishing current work, rejecting new requests |
| `STOPPED` | Gracefully terminated |
| `FAILED` | Crashed or error state |
| `QUARANTINED` | Isolated due to policy violation |
| `EVICTED` | Forcibly removed (budget, admin override) |

Phase transitions are logged and mirrored to the legacy `AgentStatus` enum for backward compatibility with the dashboard and API.

## Resource Accounting

```python
@dataclass
class ResourceUsage:
    tokens_in: int = 0          # input tokens consumed
    tokens_out: int = 0         # output tokens generated
    dollars: float = 0.0        # estimated USD spent
    tool_calls: int = 0         # number of tool invocations
    wallclock_ms: float = 0.0   # total execution time
    last_heartbeat_at: str | None = None
```

The executor updates resource usage after each invocation:

```python
self.process_table.record_usage(
    agent_id,
    tokens_out=result.tokens_used or 0,
    tool_calls=len(result.tool_calls or []),
    wallclock_ms=result.elapsed_ms or 0.0,
)
self.process_table.heartbeat(agent_id)
```

## ProcessTable API

```python
class ProcessTable:
    def register(identity, spec_ref, *, phase=Phase.ADMITTED) -> AgentProcess
    def unregister(pid) -> bool
    def get(pid) -> AgentProcess | None
    def list_all() -> list[AgentProcess]
    def transition(pid, new_phase, *, reason="", force=False) -> AgentProcess | None
    def record_usage(pid, **kwargs) -> None
    def heartbeat(pid) -> None
    def record_signal(pid, signal_name) -> None
    def clear_signal(pid, signal_name) -> None
```

## Signals

Cooperative preemption via the process table's signal queue:

```python
# Platform sends a signal
kernel.signal("agent-pid", "SIGTERM", reason="budget exceeded")

# Agent checks at next boundary
signals = kernel.check_signals("agent-pid")  # ["SIGTERM"]
# Signals are one-shot — cleared after delivery
```

Signal types the orchestrator understands:

| Signal | Meaning |
|--------|---------|
| `SIGTERM` | Request graceful shutdown; loop exits at next boundary |
| `SIGSTOP` | Pause new tool calls; agent enters DRAINING |
| `SIGEVICT` | Hard preempt (budget/policy override) |

## Integration with Executor

The executor manages the process table lifecycle:

1. **Deploy** → `process_table.register(identity, phase=ADMITTED)`
2. **Wire execution** → `transition(STARTING)` then `transition(RUNNING)`
3. **Invoke** → `heartbeat()`, then `record_usage()` after each call
4. **Stop** → `transition(DRAINING)` then `transition(STOPPED)`
5. **Failure** → `transition(FAILED)`

## Accessing from Agent Code

Via the SDK runtime:

```python
from forgeos_sdk import runtime

process = await runtime.process()
print(f"Phase: {process.phase}")
print(f"Tokens used: {process.tokens_out}")
print(f"Budget spent: ${process.dollars:.2f}")
print(f"Pending signals: {process.pending_signals}")
```

## Source Files

- `src/platform/process.py` — AgentProcess, AgentIdentity, Phase, ProcessTable, ResourceUsage
- `src/platform/executor.py` — Process lifecycle management
- `src/platform/kernel.py` — `signal()`, `check_signals()`, `attach_process_table()`
- `tests/test_platform_process.py` — Process table tests
