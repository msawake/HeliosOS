# Agent-to-Agent (A2A) Protocol

A2A is the kernel primitive for **addressed, permission-checked, framework-agnostic** agent-to-agent calls. It complements MCP (agent-to-tool) with a symmetric agent-to-agent interface.

## Why A2A

The event bus (`src/platform/event_bus.py`) provides broadcast pub/sub — good for reactive agents but not for direct delegation. A2A adds:

- **Addressed calls** — `agent__call(namespace="sales", name="cfo", task="...")`
- **Synchronous with return value** — unlike fire-and-forget events
- **Permission checks** — callee's ACL decides who may call it
- **Trace chain** — `parent_run_id` + depth propagate through nested calls
- **Loop-safe** — max depth + cycle detection
- **Framework-agnostic** — works the same whether caller is forgeos, crewai, adk, or openclaw

## The Four A2A Tools

Agents get these tools automatically when A2A is enabled:

| Tool | Purpose |
|------|---------|
| `agent__call(namespace, name, task, context, timeout)` | Synchronous call; blocks until callee returns |
| `agent__async_call(namespace, name, task, context)` | Fire-and-forget; returns `job_id` |
| `agent__await(job_id, timeout)` | Wait for an async job to complete |
| `agent__list_available(namespace, department)` | Discover callable agents |

## ACL Model — `spec.capabilities.a2a`

Every agent declares two ACLs in its manifest:

```yaml
spec:
  capabilities:
    a2a:
      canCall:
        - namespace: sales-team
          agents: [cfo, researcher]
        - namespace: legal
          roles: [senior-attorney]
      canBeCalledBy:
        - namespace: marketing
          agents: [director]
        - namespace: sales-team
          agents: [*]          # wildcard allowed
      max_depth: 5
```

**canCall** — list of peers this agent may call.
**canBeCalledBy** — list of peers allowed to call this agent. **This is the authoritative check**; the caller's `canCall` is advisory.

### Default rules (when no ACL is declared)

- Same-namespace calls are **permitted**
- Cross-namespace calls are **denied**

This gives sensible defaults and makes the "open" case require explicit opt-in.

## Call Flow

```
Agent A (sales/cfo) decides to delegate to Agent B (sales/analyst)
  |
  +-- calls tool: agent__call(namespace="sales", name="analyst", task="...")
  |
  +-- ToolExecutor routes to A2AHandler
  |
  +-- A2AHandler.call():
  |     1. Resolve callee by (namespace, name)
  |     2. Check delegation depth (max 5)
  |     3. Check for cycles in call_path
  |     4. Check callee's canBeCalledBy ACL
  |     5. Build child DelegationContext (depth+1, call_path + [callee_id])
  |     6. Invoke callee via executor.invoke()
  |     7. Return AgentResult wrapped in {success, agent_id, output, tokens, ...}
  |
  +-- Agent A receives result, continues reasoning
```

## Delegation Context

Every A2A call carries a `DelegationContext` that tracks the chain:

```python
@dataclass
class DelegationContext:
    root_run_id: str         # top-level invocation ID
    parent_run_id: str        # immediate caller's run ID
    parent_agent_id: str      # immediate caller's agent ID
    depth: int                # hops from root
    call_path: list[str]      # [agent_id, agent_id, ...]
    budget_remaining_tokens: int | None
    budget_remaining_usd: float | None
```

- **Depth** caps at `max_depth` (default 5). Exceeded → `{"success": False, "error": "Delegation depth exceeded"}`
- **Cycles** detected via `call_path` membership. Self-recursion or A→B→A detected immediately.
- **Budget propagation** — sub-agents inherit parent's remaining budget (future).

## Cross-Framework Call Example

A CrewAI agent calling an OpenClaw agent via A2A:

```python
# Inside a CrewAI tool wrapper (in a worker thread, new event loop)
async def call_openclaw_via_a2a():
    kernel = Kernel.connect()
    decision = await kernel.check_a2a_call(
        caller_agent_id=self.agent_id,
        target_namespace="operations",
        target_name="file-watcher",
    )
    if decision.denied:
        return f"Error: {decision.reason}"

    # LLM requests tool: agent__call(namespace="operations", name="file-watcher", task="...")
    # ToolExecutor routes to A2AHandler, which calls the OpenClaw adapter's invoke().
    # Returns the OpenClaw agent's output as if it were any other tool result.
```

The caller does not care that the callee runs on a different framework. The platform executor routes to the right adapter transparently.

## Failure Modes

| Scenario | Response |
|----------|----------|
| Callee agent not found | `{"success": False, "error": "Agent {ns}/{name} not found"}` |
| ACL denies caller | `{"success": False, "error": "A2A permission denied: ..."}` |
| Depth exceeded | `{"success": False, "error": "Delegation depth exceeded"}` |
| Cycle detected | `{"success": False, "error": "Delegation cycle detected"}` |
| Timeout | `{"success": False, "error": "A2A call timed out after Ns"}` |
| Callee fails | Success envelope with callee's error bubbled through |

## Testing

```bash
PYTHONPATH=. python3 -m pytest tests/test_a2a.py -v
```

10 tests cover DelegationContext, ACL checks, namespace filtering, and permission matrix.
