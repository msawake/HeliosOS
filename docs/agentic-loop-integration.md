# Helios OS Agentic Loop & Framework Integration Architecture

## Overview

Helios OS orchestrates AI agents across 7 framework adapters. Each adapter wraps a different runtime (Google ADK, CrewAI, Anthropic Agent SDK, Anthropic Managed Agents, OpenClaw, Docker Sandbox, or Helios OS native). The kernel enforces governance (permissions, budgets, policies, data boundaries) on every tool call regardless of which framework drives the agent.

The key design principle: **Helios OS never modifies agent code.** Instead, it intercepts tool execution by wrapping tools in each framework's native tool type with a kernel gate injected inside the wrapper.

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Helios OS Platform                                │
│                                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  │
│  │ Registry │  │  Kernel  │  │ Executor │  │  Audit   │  │Dashboard│  │
│  │200 agents│  │6 subsys  │  │deploy/   │  │hash-chain│  │Next.js  │  │
│  │          │  │permissions│  │invoke    │  │          │  │         │  │
│  │          │  │budgets   │  │lifecycle │  │          │  │         │  │
│  │          │  │policies  │  │sessions  │  │          │  │         │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └─────────┘  │
│                     ↑                                                    │
│           runtime.check_tool()                                           │
│           (in-process: ~0.1ms)                                           │
│                     │                                                    │
│  ┌──────────────────┴───────────────────────────────────────────────┐   │
│  │                     Stack Adapters (7)                             │   │
│  │                                                                    │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐ │   │
│  │  │ Helios OS  │ │   ADK    │ │  CrewAI  │ │   Anthropic Agent    │ │ │
│  │  │ native   │ │  Google  │ │          │ │   SDK                │ │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘ │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────────────────────┐  │   │
│  │  │ OpenClaw │ │ Sandbox  │ │   Anthropic Managed Agents       │  │   │
│  │  │          │ │ (Docker) │ │   (hosted runtime)               │  │   │
│  │  └──────────┘ └──────────┘ └──────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## The Dual-Path Pattern

Every external adapter follows the same architecture:

```python
async def invoke(self, agent_id, prompt, context, history):
    if REAL_SDK_AVAILABLE and agent_id in self._real_agents:
        return await self._invoke_via_real_sdk(...)   # Framework's own loop
    else:
        return await self._invoke_via_platform(...)   # Helios OS agentic loop
```

**Path 1 (Real SDK):** The framework (ADK Runner, CrewAI Crew, Anthropic SDK) drives the LLM loop. Helios OS tools are wrapped in the framework's native tool type with kernel gates inside.

**Path 2 (Platform Fallback):** Helios OS's `run_agentic_loop()` drives the LLM loop. Used when the external SDK is not installed or fails to initialize. Same agent behavior, different runtime.

---

## Per-Adapter Interception Details

### 1. Helios OS Native

| Aspect | Detail |
|--------|--------|
| **Stack name** | `forgeos` |
| **Who drives the loop** | Helios OS `run_agentic_loop()` — always |
| **Tool wrapping** | None — tools are dict schemas, executed via `_execute_tool()` |
| **Kernel gate location** | Inside `_execute_tool()` → `runtime.check_tool()` |
| **Interception type** | Direct — Helios OS owns the entire execution path |

```
LLM → run_agentic_loop() → _execute_tool()
                                    │
                         ┌──────────┴───────────┐
                         │ runtime.check_tool() │ ← kernel gate
                         │ tool_executor.execute│
                         └──────────────────────┘
```

### 2. Google ADK

| Aspect | Detail |
|--------|--------|
| **Stack name** | `adk` |
| **Who drives the loop** | ADK `Runner.run_async()` (real) or Helios OS loop (fallback) |
| **Framework's tool type** | `google.adk.tools.FunctionTool` — wraps an async Python function |
| **What Helios OS replaces** | The function inside FunctionTool — original tool function replaced with a wrapper |
| **Kernel gate location** | Inside the async wrapper function, before `tool_executor.execute()` |
| **Why it works** | `FunctionTool` inspects `__name__` and `__doc__` for schema — wrapper has correct name/docstring |

```
Gemini LLM → ADK Runner → FunctionTool.__call__()
                                    │
                            ┌───────┴────────┐
                            │ async _wrapper  │ ← Helios OS creates this
                            │   check_tool() │
                            │   execute()    │
                            └────────────────┘
```

**Wrapping code (`stacks/adk/adapter.py:154-183`):**
```python
async def _wrapper(**kwargs):
    # Kernel gate
    decision = await _rt.check_tool(name_captured, kwargs)
    if decision.denied:
        return {"success": False, "error": f"Kernel denied: {decision.reason}"}
    # Execute real tool
    result = await tool_executor.execute(name_captured, kwargs, agent_context)
    return result

_wrapper.__name__ = "read_json"         # ADK reads this for schema
_wrapper.__doc__ = "Read a JSON file"   # ADK reads this for description
return FunctionTool(_wrapper)           # ADK sees a normal FunctionTool
```

### 3. CrewAI

| Aspect | Detail |
|--------|--------|
| **Stack name** | `crewai` |
| **Who drives the loop** | CrewAI `Crew.kickoff()` (real) or Helios OS loop (fallback) |
| **Framework's tool type** | `crewai.tools.BaseTool` — Pydantic class with `_run()` method |
| **What Helios OS replaces** | Creates a dynamic subclass of `BaseTool` where `_run()` calls kernel first |
| **Kernel gate location** | Inside `ForgeOSTool._run()`, before `tool_executor.execute()` |
| **Sync challenge** | CrewAI's `_run()` is synchronous; kernel check uses `loop.run_until_complete()` |

```
Claude/GPT → CrewAI Crew → BaseTool._run()
                                │
                        ┌───────┴──────────┐
                        │ ForgeOSTool._run │ ← Helios OS subclass
                        │   check_tool()  │   (new event loop)
                        │   execute()     │
                        └─────────────────┘
```

**Wrapping code (`stacks/crewai/adapter.py:80-113`):**
```python
class ForgeOSTool(CrewBaseTool):
    name: str = "read_json"
    description: str = "Read a JSON file"

    def _run(self, **kwargs) -> str:
        loop = asyncio.new_event_loop()
        try:
            # Kernel gate (sync wrapper around async)
            decision = loop.run_until_complete(_rt.check_tool(name, kwargs))
            if decision.denied:
                return f"Error: Kernel denied: {decision.reason}"
            # Execute real tool
            result = loop.run_until_complete(tool_executor.execute(name, kwargs, ctx))
        finally:
            loop.close()
        return str(result)
```

### 4. Anthropic Agent SDK

| Aspect | Detail |
|--------|--------|
| **Stack name** | `anthropic-agent-sdk` |
| **Who drives the loop** | Anthropic's `sdk_query()` async iterator (real) or Helios OS loop (fallback) |
| **Framework's tool type** | In-process MCP server tools — `@sdk_tool` decorated functions |
| **What Helios OS replaces** | Tools exposed as MCP server; ONE `PreToolUse` hook gates ALL tools |
| **Kernel gate location** | Global `PreToolUse` hook — fires before EVERY tool call in the SDK |
| **Why it's elegant** | No per-tool wrapping needed — one hook intercepts everything |

```
Claude → Anthropic SDK → PreToolUse hook
                              │
                     ┌────────┴─────────┐
                     │ _forgeos_kernel_ │ ← ONE hook for ALL tools
                     │ _hook()          │
                     │  check_tool()   │
                     │  deny → block   │
                     │  allow → proceed│
                     └─────────────────┘
                              │
                     SDK executes tool
                     (MCP server call)
                              │
                     ┌────────┴─────────┐
                     │ Helios OS MCP    │
                     │ server handler   │
                     │ tool_executor    │
                     │ .execute()       │
                     └─────────────────┘
```

**The hook (`stacks/anthropic_agent/adapter.py:76-99`):**
```python
async def _forgeos_kernel_hook(input_data, tool_use_id, context):
    """ONE hook gates ALL tools."""
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    
    decision = await runtime.check_tool(tool_name, tool_input)
    if decision.denied:
        return {"hookSpecificOutput": {"permissionDecision": "deny"}}
    return {}  # allow
```

**Remote kernel variant (`stacks/anthropic_agent/adapter.py:106-135`):**
```python
# For agents running OUTSIDE Helios OS (Mode C):
hook = make_remote_kernel_hook(
    forgeos_url="https://forgeos-api.example.com",
    agent_id="my-agent",
)
# Hook calls POST /api/platform/kernel/check-tool via HTTP
```

**MCP server bridge (`stacks/anthropic_agent/adapter.py:142-181`):**
```python
# Helios OS tools exposed as in-process MCP server
@sdk_tool(name="read_json", description="...", input_schema={...})
async def handler(args):
    result = await tool_executor.execute("read_json", args, context)
    return {"content": [{"type": "text", "text": json.dumps(result)}]}

mcp_server = create_sdk_mcp_server(name="forgeos", tools=[handler, ...])
```

### 5. Anthropic Managed Agents

| Aspect | Detail |
|--------|--------|
| **Stack name** | `anthropic-managed` |
| **Who drives the loop** | Anthropic's hosted runtime (cloud) — or Helios OS loop (fallback) |
| **Framework's tool type** | Anthropic manages tools in their sandbox — Helios OS doesn't inject tools |
| **Kernel gate location** | At `invoke()` level — Helios OS checks budget/permissions BEFORE submitting to Anthropic API |
| **Interception model** | Pre-flight check, NOT per-tool. Anthropic's sandbox runs tools independently. |

```
Helios OS executor.invoke()
    │
    ├── kernel.check_budget(agent_id)      ← pre-flight check
    ├── kernel.check_permissions(agent_id)
    │
    ▼
POST /v1/agents → Anthropic Cloud
POST /v1/sessions → creates session
POST /v1/sessions/{id}/events → sends message
    │
    ▼
Anthropic's hosted sandbox executes tools independently
Helios OS polls session events for result
    │
    ▼
Record usage: tokens, cost → kernel budget tracker
```

**Key difference:** Managed Agents run tools inside Anthropic's gVisor sandbox. Helios OS cannot intercept individual tool calls. Governance is applied at the session level (budget, admission) not the tool level.

### 6. OpenClaw

| Aspect | Detail |
|--------|--------|
| **Stack name** | `openclaw` |
| **Who drives the loop** | OpenClaw Node.js gateway (real) or Helios OS loop (fallback) |
| **Framework's tool type** | HTTP POST to a tool proxy endpoint |
| **What Helios OS replaces** | Runs a `ToolProxyServer` on localhost — the gateway calls it for every tool |
| **Kernel gate location** | Inside `ToolProxyServer._process_tool_call()` |

```
LLM → OpenClaw Node.js Gateway → POST http://127.0.0.1:{port}/tool
                                        │
                               ┌────────┴──────────┐
                               │ ToolProxyServer    │ ← Helios OS HTTP server
                               │  verify token     │
                               │  bind(agent_id)   │
                               │  check_tool()     │ ← kernel gate
                               │  execute()        │
                               │  unbind()         │
                               └───────────────────┘
```

**Proxy server (`stacks/openclaw/adapter.py:52-183`):**
- Starts an HTTP server (aiohttp or FastAPI) on localhost
- OpenClaw gateway configured to POST tool calls to this endpoint
- Each request: validate agent token → bind runtime → kernel check → execute → unbind

### 7. Sandbox (Docker)

| Aspect | Detail |
|--------|--------|
| **Stack name** | `sandbox` |
| **Who drives the loop** | Agent code inside Docker container (real) or Helios OS loop (fallback) |
| **Framework's tool type** | HTTP POST to Helios OS API from inside the container |
| **What Helios OS replaces** | Container receives `FORGEOS_API_URL` — tool calls go through the API |
| **Kernel gate location** | At the Helios OS API endpoint that handles tool requests |

```
LLM (inside container) → agent code → POST {FORGEOS_API_URL}/api/tool
                                            │
                                   ┌────────┴──────────┐
                                   │ Helios OS API      │
                                   │  verify token     │
                                   │  check_tool()     │ ← kernel gate
                                   │  execute()        │
                                   └───────────────────┘
```

**Container isolation (`stacks/sandbox/adapter.py:136-183`):**
- Container spawned with `mem_limit=256m`, `cpu_quota=50000`, `read_only=True`
- Scoped token minted per invocation, revoked after completion
- Container env: `AGENT_TOKEN`, `FORGEOS_API_URL`, `AGENT_TOOLS`
- Network: internal Docker bridge (cannot reach internet directly)

---

## Interception Comparison Table

| | Helios OS | ADK | CrewAI | Anthropic SDK | Anthropic Managed | OpenClaw | Sandbox |
|---|---|---|---|---|---|---|---|
| **Framework's native tool type** | dict schema | `FunctionTool(func)` | `BaseTool._run()` | MCP server `@tool` | Hosted sandbox | HTTP POST | HTTP POST |
| **Helios OS wrapper type** | `_execute_tool()` | async wrapper → `FunctionTool` | `ForgeOSTool(BaseTool)` subclass | `PreToolUse` hook (global) | Pre-flight check | `ToolProxyServer` HTTP handler | API endpoint handler |
| **Gate strategy** | Inline in loop | Per-tool wrapper function | Per-tool class | One hook for all tools | Per-session budget check | Per-request HTTP handler | Per-request API handler |
| **Sync model** | Async native | Async native | Sync (`new_event_loop`) | Async native | Async (HTTP poll) | Async (HTTP server) | Async (HTTP API) |
| **Deny response** | `{"error": "..."}` dict | `{"success": false, "error": "..."}` dict | `"Error: ..."` string | `{"permissionDecision": "deny"}` | Invocation blocked before API call | `{"error": "..."}` JSON response | HTTP 403 |
| **Agent code change?** | No | No | No | No | No | No | No |
| **Per-tool or global?** | Per-tool | Per-tool | Per-tool | **Global (one hook)** | **Per-session** | Per-tool | Per-tool |
| **Wrapping happens at** | Never (built-in) | `create_agent()` | `create_agent()` | `invoke()` (builds MCP + hooks) | `create_agent()` (API call) | `start()` (proxy server) | `invoke()` (mint token) |

---

## How the Kernel Gate Works (Universal)

Regardless of framework, the kernel check follows the same logic:

```
runtime.check_tool(tool_name, tool_input)
    │
    ▼
kernel.check_tool_call(agent_id, tool_name, tool_input, estimated_cost)
    │
    ├── 1. PermissionManager
    │       Is tool_name in agent's allowed list? (wildcard matching)
    │       Is tool_name in agent's denied list?
    │       → ALLOW or DENY
    │
    ├── 2. BudgetManager
    │       Today's spend + reservations < daily_usd limit?
    │       This call's estimated cost < per_task_usd limit?
    │       → ALLOW or RATE_LIMIT
    │
    ├── 3. PolicyEngine
    │       Evaluate declarative JSON-logic rules
    │       → ALLOW or DENY
    │
    ├── 4. DataBoundaryManager
    │       Target namespace in allowed list?
    │       Target namespace not in blocked list?
    │       → ALLOW or DENY
    │
    └── 5. CapabilityManager
            Valid capability token? (bypasses ACLs)
            → ALLOW (short-circuit)
```

**Time: ~0.1ms in-process, ~50-100ms via HTTP (remote agents).**

---

## SDK Detection and Fallback

Every adapter gracefully degrades when the external SDK is not installed:

| Framework | Detection | Fallback |
|-----------|-----------|----------|
| ADK | `from google.adk import Agent, Runner` | `run_agentic_loop()` with Gemini via `llm_router` |
| CrewAI | `from crewai import Agent, Task, Crew` | `run_agentic_loop()` with Claude/GPT via `llm_router` |
| Anthropic Agent SDK | `from claude_agent_sdk import query` | `run_agentic_loop()` with Claude via `llm_router` |
| Anthropic Managed | API key + beta access | `run_agentic_loop()` with Claude via `llm_router` |
| OpenClaw | Node.js gateway binary | `run_agentic_loop()` via `llm_router` |
| Sandbox | `import docker` | `run_agentic_loop()` via `llm_router` |
| Helios OS | Always available | N/A (this IS the fallback) |

**Result:** An agent deployed with `stack: adk` works even if `google-adk` is not installed — it just uses Helios OS's native loop instead of ADK's Runner.

---

## YAML-Only Configuration

All governance is declared in the agent manifest. No code changes to the agent:

```yaml
apiVersion: agentos/v1
kind: Agent
metadata:
  name: ad-processor
  namespace: marketing
spec:
  stack: adk                           # ← which adapter to use
  llm:
    chat_model: gemini-2.0-flash
  tools:                               # ← what tools exist
    - read_json
    - write_json
    - transform_ad
  capabilities:
    tools:
      allowed: [read_json, write_json, transform_ad]
      denied: [delete_file, send_email]    # ← what's forbidden
    a2a:
      canBeCalledBy:
        - namespace: sales
          agents: ["*"]
  boundaries:
    budgets:
      daily_usd: 5.00                     # ← spending limit
      per_task_usd: 0.50
    data:
      allowed_namespaces: [marketing, sales]
      pii_policy: mask
  governance:
    audit_level: full
    human_in_loop:
      - event: approve_discount
        approvers: [marketing-manager]
```

Helios OS reads this YAML and automatically:
1. Creates tool wrappers with kernel gates for the chosen framework
2. Registers the agent in the kernel with its permissions/budgets/policies
3. Wires execution lifecycle (reflex/scheduled/always_on/autonomous)
4. Deploys with the correct adapter

---

## Known Gaps

| Gap | Affected Adapters | Impact | Status |
|-----|-------------------|--------|--------|
| No cost tracking in real SDK paths | ADK, CrewAI, OpenClaw | Budget enforcement weakened | Planned fix |
| No callbacks (GUIDE steering) in real SDK paths | ADK, CrewAI | Steering handlers don't fire | Planned fix |
| No event-sourced session emission in real SDK paths | ADK, CrewAI, OpenClaw | Event log incomplete | Planned fix |
| Conversation managers only in Helios OS loop | ADK, CrewAI | SDKs manage own context | By design |
| Managed Agents: no per-tool kernel gate | Anthropic Managed | Anthropic's sandbox runs tools unmonitored | Architecture limitation |
