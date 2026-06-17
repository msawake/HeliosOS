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

---

## Remote Kernel Mode (HTTP) — Distributed Agent Deployment

### When Remote Mode Activates

The Helios OS SDK has two backends that are transparently swapped:

| Mode | When | Backend Class | Latency |
|------|------|---------------|---------|
| **In-Process** | Agent runs inside Helios OS bootstrap | `_InProcessBackend` | ~0.1ms (direct Python call) |
| **Remote (HTTP)** | Agent runs on separate Cloud Run / VM / container | `_HTTPBackend` | ~50-100ms (HTTP round-trip) |

**Auto-detection** (`Kernel.connect()`):
```python
@classmethod
def connect(cls) -> Kernel:
    if cls._process_local is not None:
        return cls._process_local          # In-process — direct call
    base_url = os.environ.get("FORGEOS_API_URL", "http://localhost:5000")
    api_key = os.environ.get("FORGEOS_API_KEY")
    return cls.remote(base_url, api_key)   # Remote — HTTP calls
```

The agent code doesn't change. `runtime.check_tool()` works identically in both modes — the backend is the only difference.

### Architecture: Remote Kernel Deployment

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Google Cloud                                    │
│                                                                          │
│  ┌──────────────────────────────┐    ┌────────────────────────────────┐ │
│  │  Cloud Run: Helios OS Control   │    │  Cloud Run: Agent Fleet        │ │
│  │  Plane (kernel lives HERE)    │    │  (agents run HERE)             │ │
│  │                                │    │                                │ │
│  │  ┌────────┐  ┌─────────────┐ │    │  ┌─────┐ ┌─────┐ ┌─────┐    │ │
│  │  │ Kernel │  │  FastAPI    │◀├────├──│ #1  │ │ #2  │ │ #50 │    │ │
│  │  │        │  │  /kernel/*  │ │HTTP│  │ ADK │ │CrewAI│ │Anthr│    │ │
│  │  │ perms  │  │  endpoints  │ │    │  │     │ │     │ │ SDK │    │ │
│  │  │ budget │  └─────────────┘ │    │  └──┬──┘ └──┬──┘ └──┬──┘    │ │
│  │  │ policy │                   │    │     │       │       │        │ │
│  │  │ audit  │  ┌─────────────┐ │    │     └───────┴───────┘        │ │
│  │  └────────┘  │  Dashboard  │ │    │           │                   │ │
│  │              └─────────────┘ │    │    forgeos_sdk.runtime        │ │
│  │                               │    │    .check_tool() → HTTP      │ │
│  └──────────────────────────────┘    └────────────────────────────────┘ │
│                                                                          │
│  ┌──────────────────────────────┐                                       │
│  │  Cloud SQL (shared state)     │                                       │
│  │  - agent registry             │                                       │
│  │  - budget tracking            │                                       │
│  │  - audit trail                │                                       │
│  │  - session events             │                                       │
│  └──────────────────────────────┘                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

### HTTP API Surface (Kernel Endpoints)

The Helios OS control plane exposes 6 kernel endpoints at `src/dashboard/fastapi_app.py`:

| Endpoint | Method | Purpose | Request Body | Response |
|----------|--------|---------|-------------|----------|
| `/api/platform/kernel/check-tool` | POST | Permission + budget check for a tool call | `{agent_id, tool_name, tool_input, estimated_cost_usd}` | `KernelDecision {action, reason, details}` |
| `/api/platform/kernel/check-a2a` | POST | Permission check for agent-to-agent call | `{caller_agent_id, target_namespace, target_name}` | `KernelDecision {action, reason, details}` |
| `/api/platform/kernel/check-data` | POST | Namespace boundary check | `{agent_id, target_namespace}` | `KernelDecision {action, reason, details}` |
| `/api/platform/kernel/contract/{agent_id}` | GET | Retrieve agent's full contract | — | Contract dict or 404 |
| `/api/platform/kernel/admit` | POST | Validate a contract before deploy | Contract dict | `AdmissionResult {admitted, errors, warnings}` |
| `/api/platform/kernel/audit` | POST | Record a custom audit event | `{agent_id, event, details}` | `{"ok": true}` |

### Request/Response Flow (Step by Step)

**Scenario:** ADK agent on Cloud Run #2 calls `read_json` tool.

```
Step 1: Agent code calls runtime.check_tool("read_json", {"file": "ads.json"})

Step 2: Runtime calls self._kernel.check_tool_call(...)
        → self._kernel is SDK Kernel with _HTTPBackend

Step 3: _HTTPBackend.check_tool_call() sends:
        POST https://forgeos-api-xxx.europe-west1.run.app/api/platform/kernel/check-tool
        Headers:
          Content-Type: application/json
          X-API-Key: fos_mktg_xxxx
        Body:
          {
            "agent_id": "ad-text-processor",
            "tool_name": "read_json",
            "tool_input": {"file": "ads.json"},
            "estimated_cost_usd": null
          }

Step 4: Helios OS FastAPI receives request
        → _require_kernel() gets the platform Kernel instance
        → kernel.check_tool_call("ad-text-processor", "read_json", {...}, null)

Step 5: Kernel runs checks (in-memory, ~0.1ms):
        1. PermissionManager: "read_json" in allowed list? → YES ✅
        2. BudgetManager: $1.20 spent today < $5.00 limit? → YES ✅
        3. PolicyEngine: any policies deny? → NO ✅
        → KernelDecision(action="allow", reason="permitted")

Step 6: FastAPI returns:
        HTTP 200
        {
          "action": "allow",
          "reason": "permitted",
          "details": {"tool_name": "read_json", "agent_id": "ad-text-processor"},
          "audit_id": "a1b2c3d4",
          "timestamp": "2026-04-26T14:30:00Z"
        }

Step 7: _HTTPBackend deserializes → KernelDecision.from_dict()
        → Returns to runtime → Returns to agent code

Step 8: Agent sees decision.allowed == True → executes the tool
```

### Setting Up a Remote Agent (3 Modes)

#### Mode A: Helios OS SDK + In-Process Kernel (Default)

Agent runs inside Helios OS. Kernel is in the same process.

```python
# Nothing to configure — bootstrap wires everything
# runtime.check_tool() → direct Python call → ~0.1ms
```

#### Mode B: Helios OS SDK + Remote Kernel (Separate Cloud Run)

Agent runs on its own Cloud Run. Helios OS runs on another.

```python
# Agent's Cloud Run environment:
FORGEOS_API_URL=https://forgeos-api-xxx.europe-west1.run.app
FORGEOS_API_KEY=fos_mktg_xxxx
FORGEOS_AGENT_ID=ad-text-processor
FORGEOS_NAMESPACE=marketing

# Agent code:
from forgeos_sdk.runtime import runtime
from forgeos_sdk.kernel import Kernel

kernel = Kernel.connect()  # auto-detects remote (no local instance)
runtime.register_platform(kernel=kernel)
runtime.bind(os.environ["FORGEOS_AGENT_ID"], namespace=os.environ["FORGEOS_NAMESPACE"])

# Now runtime.check_tool() → HTTP POST to Helios OS → ~50-100ms
```

#### Mode C: Framework Hook + Remote Kernel (No SDK Wrapping)

Agent uses the Anthropic Agent SDK's native hook system to call Helios OS kernel via HTTP.
No tool wrapping needed — one hook intercepts everything.

```python
# Agent code (using Anthropic Agent SDK):
from claude_agent_sdk import query, ClaudeAgentOptions
from stacks.anthropic_agent.adapter import make_remote_kernel_hook

# Create a hook that calls Helios OS kernel via HTTP
kernel_hook = make_remote_kernel_hook(
    forgeos_url="https://forgeos-api-xxx.europe-west1.run.app",
    agent_id="my-agent",
)

options = ClaudeAgentOptions(
    hooks={"PreToolUse": [HookMatcher(matcher="*", hooks=[kernel_hook])]},
    # ...
)

async for msg in query(prompt="...", options=options):
    print(msg)
```

The hook internally does:
```python
async def _hook(input_data, tool_use_id, context):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{forgeos_url}/api/platform/kernel/check-tool",
            json={"agent_id": agent_id, "tool_name": ..., "tool_input": ...},
        )
        if resp.json().get("action") == "deny":
            return {"hookSpecificOutput": {"permissionDecision": "deny"}}
    return {}  # allow
```

### Remote Kernel: Per-Framework Integration

| Framework | Remote Integration Method | How Kernel is Called |
|-----------|--------------------------|---------------------|
| **Helios OS native** | Set `FORGEOS_API_URL` env var | SDK `_HTTPBackend` in `_execute_tool()` |
| **ADK** | Set `FORGEOS_API_URL` + `pip install forgeos-sdk` | SDK `_HTTPBackend` inside `FunctionTool` wrapper |
| **CrewAI** | Set `FORGEOS_API_URL` + `pip install forgeos-sdk` | SDK `_HTTPBackend` inside `BaseTool._run()` wrapper |
| **Anthropic Agent SDK** | Use `make_remote_kernel_hook(url, agent_id)` | Direct HTTP POST in `PreToolUse` hook (no SDK needed) |
| **Anthropic Managed** | Helios OS control plane calls Anthropic API | Kernel checked before session creation (pre-flight) |
| **OpenClaw** | Gateway connects to `ToolProxyServer` on Helios OS host | ToolProxyServer handler calls kernel directly |
| **Sandbox** | Container receives `FORGEOS_API_URL` in env | HTTP POST from container to Helios OS API endpoint |

### Authentication for Remote Kernel

```
Remote Agent                          Helios OS Control Plane
    │                                        │
    │  POST /kernel/check-tool               │
    │  Header: X-API-Key: fos_mktg_xxxx     │
    │──────────────────────────────────────▶ │
    │                                        │
    │                          ┌──────────────┤
    │                          │ Validate key │
    │                          │ Map to tenant│
    │                          │ Run kernel   │
    │                          └──────────────┤
    │                                        │
    │  ◀───── {"action": "allow"}            │
    │                                        │
```

**API key scoping (planned):**
- `fos_sales_xxxx` → can only register/check agents in `namespace=sales`
- `fos_mktg_xxxx` → can only register/check agents in `namespace=marketing`
- `fos_admin_xxxx` → full access across all namespaces

**Alternative: Google Service Account auth:**
- Agent's Cloud Run has a service account
- Helios OS validates via IAM
- No API key needed — identity from Google's infrastructure

### Latency Impact at Scale

| Metric | In-Process | Remote (same region) | Remote (cross-region) |
|--------|-----------|--------------------|--------------------|
| Kernel check latency | ~0.1ms | ~50-100ms | ~150-300ms |
| Per-invocation overhead (10 tools) | ~1ms | ~500ms-1s | ~1.5-3s |
| LLM call latency (for comparison) | 5-30s | 5-30s | 5-30s |
| Overhead as % of total | 0.003% | 1-3% | 5-10% |

**Conclusion:** Even cross-region, the kernel HTTP overhead is negligible compared to LLM latency.

### Scaling the Control Plane

```
50 remote agents × 10 tool calls/invocation × 5 invocations/hour
= 2,500 kernel checks/hour = ~42/minute

Cloud Run auto-scaling:
- 1 instance handles 5,000+ req/sec (FastAPI + async)
- Kernel check CPU: ~0.1ms per check
- 42 req/min is 0.07 req/sec — trivially handled by 1 instance

Scaling concern starts at:
- 10,000+ agents with continuous loops = ~100 req/sec
- Solution: Redis cache for hot ALLOW decisions (30s TTL)
- Budget checks always real-time (can't cache a depleting budget)
```

### Usage Reporting (Remote → Control Plane)

Remote agents must report LLM token usage back to Helios OS for budget tracking:

```python
# After each LLM call in the remote agent:
await runtime.record_usage(
    tokens_in=response.usage.input_tokens,
    tokens_out=response.usage.output_tokens, 
    cost_usd=estimated_cost,
)
# → POST /api/platform/kernel/usage (planned endpoint)
# → Kernel updates budget tracker for this agent
# → Next check_tool() includes this spend in budget calculation
```

**Without usage reporting:** The kernel allows all tool calls because it thinks the agent hasn't spent anything. Budget enforcement is toothless.

**Planned endpoint:**
```
POST /api/platform/kernel/usage
{
  "agent_id": "ad-text-processor",
  "tokens_in": 1500,
  "tokens_out": 800,
  "cost_usd": 0.03,
  "model": "gemini-2.0-flash"
}
→ {"ok": true, "daily_spent": 1.23, "daily_limit": 5.00}
```
x
