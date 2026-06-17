# Tool Enforcement Across Adapters

Every tool call in Helios OS passes through the kernel before execution — regardless of which framework adapter runs the agent. This document explains the enforcement mechanism for each adapter.

## The Core Gate

All enforcement converges on one check:

```python
from src.forgeos_sdk.runtime import runtime

decision = await runtime.check_tool(tool_name, tool_input)
if decision.denied:
    return {"error": f"Kernel denied: {decision.reason}"}
# else: proceed to tool_executor.execute()
```

What `check_tool` does internally:

1. **Permission check** — is the tool in the agent's allowed list? Is it in the deny list?
2. **Budget check** — if `estimated_cost_usd` is provided, does it fit within per-task/daily limits?
3. **Policy evaluation** — do any declarative `deny_if` rules match?
4. **Audit recording** — log the decision (allow or deny) with full context

## Helios OS (Native)

**File**: `src/platform/agentic_loop.py` — `_execute_tool()`

The kernel gate runs before every tool execution in the agentic loop:

```python
async def _execute_tool(tool_name, tool_input, tool_executor, agent_context, ...):
    # Kernel gate
    from src.forgeos_sdk.runtime import runtime as _rt
    if _rt.is_registered and _rt.is_bound:
        decision = await _rt.check_tool(tool_name, tool_input)
        if decision.denied:
            return {"error": f"Kernel denied: {decision.reason}"}

    # Execute tool (only if allowed)
    result = await tool_executor.execute(tool_name, tool_input, agent_context)
    return result
```

Both `run_agentic_loop()` (sync) and `run_agentic_loop_with_events()` (streaming) call `_execute_tool`, so both paths are gated.

## CrewAI

**File**: `stacks/crewai/adapter.py` — `_build_crewai_tools()`

CrewAI tools are wrapped as `BaseTool` subclasses. The kernel gate runs inside `_run()`:

```python
class ForgeOSTool(CrewBaseTool):
    def _run(self, **kwargs) -> str:
        loop = asyncio.new_event_loop()
        try:
            # Kernel gate
            from src.forgeos_sdk.runtime import runtime as _rt
            if _rt.is_registered and _rt.is_bound:
                decision = loop.run_until_complete(_rt.check_tool(name, kwargs))
                if decision.denied:
                    return f"Error: Kernel denied: {decision.reason}"

            # Execute tool
            result = loop.run_until_complete(
                tool_executor.execute(name, kwargs, agent_context)
            )
        finally:
            loop.close()
        return str(result)
```

**Why a new event loop?** CrewAI's `Crew.kickoff()` runs in a worker thread via `run_in_executor`. That thread has no running asyncio loop, so the wrapper creates one per tool call. This is safe because the thread is dedicated to that crew execution.

## Google ADK

**File**: `stacks/adk/adapter.py` — `_build_adk_tools()`

ADK tools are wrapped as `FunctionTool` async callables. The kernel gate runs before execution:

```python
async def _wrapper(**kwargs):
    # Kernel gate
    from src.forgeos_sdk.runtime import runtime as _rt
    if _rt.is_registered and _rt.is_bound:
        decision = await _rt.check_tool(tool_name, kwargs)
        if decision.denied:
            return {"success": False, "error": f"Kernel denied: {decision.reason}"}

    # Execute tool
    result = await tool_executor.execute(tool_name, kwargs, agent_context)
    return result

tool = FunctionTool(_wrapper)
```

This is simpler than CrewAI because ADK's `Runner.run_async()` already runs in an async context.

## OpenClaw

**File**: `stacks/openclaw/adapter.py` — `ToolProxyServer`

OpenClaw agents run in a separate Node.js process. Tool enforcement is via an HTTP proxy:

```
OpenClaw Gateway (Node.js)
  │ POST /tool
  │ Header: X-Agent-Token: sbx_...
  │ Body: {"tool_name": "...", "tool_input": {...}}
  ▼
ToolProxyServer (Python, port 18790)
  1. Validate token (SandboxTokenStore.verify)
  2. Bind SDK runtime (agent_id from token claims)
  3. runtime.check_tool() → kernel permission check
  4. tool_executor.execute() → result
  5. Unbind runtime
  6. Return JSON result
```

**Token minting**: when `create_agent()` is called, the adapter mints a scoped token via `SandboxTokenStore`. The token encodes `agent_id`, `namespace`, `tools`, and `tier`. It's written into the SKILLS/default.yaml so the OpenClaw gateway includes it in every tool request.

**SKILLS/default.yaml** (auto-generated):

```yaml
- name: company__search_knowledge
  method: POST
  endpoint: "http://127.0.0.1:18790/tool"
  headers:
    X-Agent-Token: "sbx_abc123..."
  body:
    tool_name: "company__search_knowledge"
    tool_input: "{{params}}"
```

## Sandbox

**File**: `stacks/sandbox/adapter.py` + `src/forgeos_sandbox/runner.py`

Sandbox agents run in Docker containers. Same token-based proxy pattern as OpenClaw, but using the FastAPI endpoint:

```
Docker Container (forgeos-sandbox:latest)
  │ POST /api/sandbox/tool
  │ Header: X-Agent-Token: sbx_...
  ▼
FastAPI endpoint (src/dashboard/fastapi_app.py)
  1. SandboxTokenStore.verify(token)
  2. Kernel permission check
  3. tool_executor.execute()
  4. Return result
```

The container runner (`src/forgeos_sandbox/runner.py`) also fetches tool schemas via `GET /api/platform/tools` at startup.

## Fallback Path (All Adapters)

When the real SDK is unavailable (CrewAI not installed, ADK not installed, OpenClaw gateway down, Docker unavailable), all adapters fall back to the platform agentic loop:

```
adapter.invoke() → run_agentic_loop() → _execute_tool() → kernel gate → tool_executor
```

This path always has full kernel enforcement because `_execute_tool()` contains the gate.

## Enforcement Summary

| Adapter | Real SDK Path | Gate Location | Fallback |
|---------|--------------|---------------|----------|
| Helios OS | agentic_loop | `_execute_tool()` | — |
| CrewAI | Crew.kickoff() | `BaseTool._run()` | agentic_loop |
| ADK | Runner.run_async() | `FunctionTool._wrapper()` | agentic_loop |
| OpenClaw | Node.js gateway | `ToolProxyServer` HTTP | agentic_loop |
| Sandbox | Docker container | `/api/sandbox/tool` HTTP | agentic_loop |

**All paths enforced.** No tool call reaches `tool_executor.execute()` without passing through the kernel.

## Backward Compatibility

When the runtime is not registered or not bound (e.g., running without a kernel), the gate is a no-op:

```python
if _rt.is_registered and _rt.is_bound:
    # gate runs
else:
    # no-op, tool executes without checks
```

This means existing setups that don't wire a kernel keep working unchanged.

## Source Files

- `src/platform/agentic_loop.py` — `_execute_tool()` kernel gate
- `stacks/crewai/adapter.py` — `_build_crewai_tools()` → `BaseTool._run()` gate
- `stacks/adk/adapter.py` — `_build_adk_tools()` → `_wrapper()` gate
- `stacks/openclaw/adapter.py` — `ToolProxyServer._process_tool_call()`
- `stacks/sandbox/adapter.py` — `SandboxTokenStore`
- `src/dashboard/fastapi_app.py` — `/api/sandbox/tool` endpoint
- `tests/test_kernel_tool_gate.py` — Kernel gate integration tests
- `tests/test_openclaw_tool_proxy.py` — OpenClaw proxy tests
- `tests/test_crewai_adk_kernel_gate.py` — CrewAI/ADK gate tests
