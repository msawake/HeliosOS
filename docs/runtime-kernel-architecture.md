# Runtime ↔ Kernel Architecture — Detailed Diagrams

How `runtime` and `kernel` interact in both modes: in-process (direct Python) and HTTP (remote Cloud Run).

---

## 1. The Two Modes at a Glance

```
MODE A: IN-PROCESS (agent runs inside Helios OS)
═══════════════════════════════════════════════

  ┌──────────────────────────────────────────────────────────────────┐
  │                    SINGLE PYTHON PROCESS                          │
  │                    (one Cloud Run container)                      │
  │                                                                   │
  │  Agent Code ──→ runtime ──→ _InProcessBackend ──→ Kernel         │
  │                 (0.1ms)     (direct function call)                │
  └──────────────────────────────────────────────────────────────────┘


MODE C: HTTP (agent runs on separate Cloud Run)
═══════════════════════════════════════════════

  ┌──────────────────────┐         ┌──────────────────────────────┐
  │  AGENT CLOUD RUN      │  HTTP   │  HELIOS OS CLOUD RUN          │
  │                       │ (~50ms) │                               │
  │  Agent ──→ runtime    │────────▶│  FastAPI ──→ Kernel           │
  │            │          │         │                               │
  │     _HTTPBackend      │         │  /api/platform/kernel/*       │
  └──────────────────────┘         └──────────────────────────────┘
```

---

## 2. Mode A: In-Process — Full Detail

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         HELIOS OS CLOUD RUN CONTAINER                        │
│                         (forgeos-api on port 5000)                            │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  LAYER 1: API REQUEST                                                │    │
│  │                                                                       │    │
│  │  User → POST /api/platform/agents/{id}/invoke                        │    │
│  │       → FastAPI endpoint (src/dashboard/fastapi_app.py:774)           │    │
│  │       → calls platform_executor.invoke(agent_id, prompt)              │    │
│  └──────────────────────────────┬────────────────────────────────────────┘    │
│                                 │                                             │
│  ┌──────────────────────────────▼────────────────────────────────────────┐    │
│  │  LAYER 2: EXECUTOR (src/platform/executor.py)                         │    │
│  │                                                                       │    │
│  │  invoke(agent_id, prompt):                                            │    │
│  │    ① agent_def = registry.get(agent_id)         # look up agent       │    │
│  │    ② history = session_store.get(session_id)     # load history       │    │
│  │    ③ process_table.heartbeat(agent_id)           # mark alive         │    │
│  │                                                                       │    │
│  │    ④ ┌──────────────────────────────────────────┐                     │    │
│  │      │  runtime.bind(agent_id, namespace)       │  ← SETS IDENTITY   │    │
│  │      │  (src/forgeos_sdk/runtime.py:226)        │                     │    │
│  │      │                                          │                     │    │
│  │      │  Sets contextvars.ContextVar:             │                     │    │
│  │      │    _agent_ctx = {                         │                     │    │
│  │      │      "agent_id": "3cd5d08f-5f4",         │                     │    │
│  │      │      "namespace": "support"              │                     │    │
│  │      │    }                                     │                     │    │
│  │      └──────────────────────────────────────────┘                     │    │
│  │                                                                       │    │
│  │    ⑤ result = adapter.invoke(agent_id, prompt, history)               │    │
│  │    ⑥ process_table.record_usage(tokens, tool_calls)                   │    │
│  │    ⑦ runtime.unbind(token)                       ← CLEARS IDENTITY   │    │
│  └──────────────────────────────┬────────────────────────────────────────┘    │
│                                 │                                             │
│  ┌──────────────────────────────▼────────────────────────────────────────┐    │
│  │  LAYER 3: AGENTIC LOOP (src/platform/agentic_loop.py)                 │    │
│  │                                                                       │    │
│  │  run_agentic_loop(llm_router, llm_config, prompt, tools, ...):        │    │
│  │                                                                       │    │
│  │    LOOP:                                                              │    │
│  │    ┌──────────────────────────────────────────────┐                   │    │
│  │    │  ① response = llm_router.chat(messages, tools)                   │    │
│  │    │     → Calls Gemini/Claude/GPT via provider API                   │    │
│  │    │     → Returns: text + tool_calls[]                               │    │
│  │    │                                                                  │    │
│  │    │  ② For each tool_call:                                           │    │
│  │    │     _execute_tool(tool_name, tool_input, ...)                    │    │
│  │    │       │                                                          │    │
│  │    │       ▼                                                          │    │
│  │    │     ┌────────────────────────────────────────┐                   │    │
│  │    │     │  KERNEL GATE (line 428-438)            │                   │    │
│  │    │     │                                        │                   │    │
│  │    │     │  from forgeos_sdk.runtime import runtime│                  │    │
│  │    │     │  if runtime.is_registered and is_bound: │                  │    │
│  │    │     │    decision = await runtime.check_tool( │                  │    │
│  │    │     │      tool_name, tool_input             │                   │    │
│  │    │     │    )                                   │                   │    │
│  │    │     │    if decision.denied:                 │                   │    │
│  │    │     │      return {"error": "Kernel denied"} │                   │    │
│  │    │     └───────────────┬────────────────────────┘                   │    │
│  │    │                     │                                            │    │
│  │    │                     ▼                                            │    │
│  │    │     ┌────────────────────────────────────────┐                   │    │
│  │    │     │  tool_executor.execute(name, input)    │                   │    │
│  │    │     │  → Routes to MCP/A2A/memory/custom     │                   │    │
│  │    │     └────────────────────────────────────────┘                   │    │
│  │    │                                                                  │    │
│  │    │  ③ Append tool_result to messages                                │    │
│  │    │  ④ LOOP back to ① until stop_reason == "end_turn"               │    │
│  │    └──────────────────────────────────────────────┘                   │    │
│  └──────────────────────────────┬────────────────────────────────────────┘    │
│                                 │                                             │
│  ┌──────────────────────────────▼────────────────────────────────────────┐    │
│  │  LAYER 4: RUNTIME → KERNEL (the check_tool call path)                 │    │
│  │                                                                       │    │
│  │  runtime.check_tool("approve_discount", {"value": 500})               │    │
│  │    │  (src/forgeos_sdk/runtime.py:264)                                │    │
│  │    │                                                                  │    │
│  │    │  self._kernel = <Kernel object>     ← set by register_platform() │    │
│  │    │  self._kernel is the ACTUAL platform kernel (same memory)        │    │
│  │    │                                                                  │    │
│  │    ▼                                                                  │    │
│  │  runtime._require_kernel()                                            │    │
│  │    → returns self._kernel  (type: _InProcessBackend)                  │    │
│  │    │  (src/forgeos_sdk/kernel.py:204)                                 │    │
│  │    │                                                                  │    │
│  │    ▼                                                                  │    │
│  │  _InProcessBackend.check_tool_call(agent_id, tool_name, tool_input)   │    │
│  │    │  (src/forgeos_sdk/kernel.py:210)                                 │    │
│  │    │                                                                  │    │
│  │    │  self._k = <platform Kernel object>   ← direct reference         │    │
│  │    │  return self._k.check_tool_call(...)  ← DIRECT PYTHON CALL       │    │
│  │    │                                                                  │    │
│  │    ▼                                                                  │    │
│  │  ┌────────────────────────────────────────────────────────────────┐   │    │
│  │  │  PLATFORM KERNEL (src/platform/kernel/_facade.py:907)          │   │    │
│  │  │                                                                │   │    │
│  │  │  def check_tool_call(self, agent_id, tool_name, ...):          │   │    │
│  │  │                                                                │   │    │
│  │  │    ① PermissionManager.check_tool_call()  (line 261)           │   │    │
│  │  │       │ Look up agent in registry                              │   │    │
│  │  │       │ Read tools from agent_def.tools                        │   │    │
│  │  │       │ Check if tool_name matches allowed list (with wildcards)│  │    │
│  │  │       │ Check if tool_name in denied list                      │   │    │
│  │  │       └→ KernelDecision(allow) or KernelDecision(deny)         │   │    │
│  │  │                                                                │   │    │
│  │  │    ② BudgetManager.check_budget()  (line 363)                  │   │    │
│  │  │       │ Read boundaries.budgets from contract                  │   │    │
│  │  │       │ Read current spend from process table                  │   │    │
│  │  │       │ Compare: spent + estimated > daily_limit?              │   │    │
│  │  │       └→ KernelDecision(allow) or KernelDecision(rate_limit)   │   │    │
│  │  │                                                                │   │    │
│  │  │    ③ PolicyEngine.evaluate()  (line 556)                       │   │    │
│  │  │       │ Check declarative policies (OPA/JSON-logic)            │   │    │
│  │  │       └→ KernelDecision(allow) or KernelDecision(deny)         │   │    │
│  │  │                                                                │   │    │
│  │  │    ④ Audit: record decision in hash-chained log                │   │    │
│  │  │                                                                │   │    │
│  │  │    return KernelDecision(action="allow"|"deny", reason="...")   │   │    │
│  │  └────────────────────────────────────────────────────────────────┘   │    │
│  │                                                                       │    │
│  │  TOTAL LATENCY: ~0.1ms (in-memory dict lookups, no I/O)              │    │
│  └───────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Mode C: HTTP — Full Detail

```
┌───────────────────────────────────────────────────────────────────────────────┐
│  AGENT CLOUD RUN (e.g., research-claude-sdk, mode-c-adk)                       │
│                                                                                │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  LAYER 1: AGENT CODE                                                      │ │
│  │                                                                           │ │
│  │  # Any framework: ADK, CrewAI, Claude SDK, OpenAI, or custom              │ │
│  │  # The agent runs its own LLM loop on its own Cloud Run                   │ │
│  │                                                                           │ │
│  │  from forgeos_sdk.runtime import runtime                                  │ │
│  │  from forgeos_sdk.kernel import Kernel                                    │ │
│  │                                                                           │ │
│  │  # AT STARTUP (once):                                                     │ │
│  │  kernel = Kernel.connect()                                                │ │
│  │    │  (src/forgeos_sdk/kernel.py:122)                                     │ │
│  │    │                                                                      │ │
│  │    │  ① Try Kernel.local()                                                │ │
│  │    │     → Checks cls._local_kernel (set by register_local_instance)      │ │
│  │    │     → None (no local kernel — we're on separate Cloud Run)           │ │
│  │    │                                                                      │ │
│  │    │  ② Fallback: Kernel.remote(FORGEOS_API_URL)                          │ │
│  │    │     → Reads FORGEOS_API_URL from env var                             │ │
│  │    │     → Creates _HTTPBackend(base_url, api_key)                        │ │
│  │    │     → Returns Kernel wrapping _HTTPBackend                           │ │
│  │    │                                                                      │ │
│  │    ▼                                                                      │ │
│  │  runtime.register_platform(kernel=kernel)                                 │ │
│  │    → self._kernel = kernel  (type: Kernel with _HTTPBackend inside)       │ │
│  │                                                                           │ │
│  │  runtime.bind("agent-id-from-env", namespace="research")                  │ │
│  │    → Sets _agent_ctx ContextVar                                           │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                                │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  LAYER 2: TOOL CALL INTERCEPTION                                          │ │
│  │                                                                           │ │
│  │  The agent's LLM decides to call a tool. The platform's hook fires:       │ │
│  │                                                                           │ │
│  │  ┌──────────────────────────────────────────────────┐                     │ │
│  │  │  ADK:    FunctionTool._wrapper(**kwargs)          │                     │ │
│  │  │  CrewAI: ForgeOSTool._run(**kwargs)               │                     │ │
│  │  │  Claude: _forgeos_kernel_hook(input_data, ...)    │                     │ │
│  │  │  OpenAI: ForgeOSKernelHooks.on_tool_start(...)    │                     │ │
│  │  │  Custom: direct runtime.check_tool() call         │                     │ │
│  │  └──────────────────────┬───────────────────────────┘                     │ │
│  │                         │                                                 │ │
│  │                         ▼                                                 │ │
│  │  decision = await runtime.check_tool("tool_name", {"input": "..."})       │ │
│  │    │  (src/forgeos_sdk/runtime.py:264)                                    │ │
│  │    │                                                                      │ │
│  │    │  self._kernel = <Kernel with _HTTPBackend>                           │ │
│  │    │                                                                      │ │
│  │    ▼                                                                      │ │
│  │  _HTTPBackend.check_tool_call(agent_id, tool_name, tool_input, cost)      │ │
│  │    │  (src/forgeos_sdk/kernel.py:265)                                     │ │
│  │    │                                                                      │ │
│  │    │  httpx.AsyncClient.post(                                             │ │
│  │    │    "/api/platform/kernel/check-tool",                                │ │
│  │    │    json={                                                            │ │
│  │    │      "agent_id": "3cd5d08f-5f4",                                     │ │
│  │    │      "tool_name": "approve_discount",                                │ │
│  │    │      "tool_input": {"value": 500},                                   │ │
│  │    │      "estimated_cost_usd": 0.01                                      │ │
│  │    │    }                                                                 │ │
│  │    │  )                                                                   │ │
│  │    │                                                                      │ │
│  └────┼──────────────────────────────────────────────────────────────────────┘ │
│       │                                                                        │
└───────┼────────────────────────────────────────────────────────────────────────┘
        │
        │  HTTP POST (network round-trip ~50ms)
        │  https://forgeos-api.example.com/api/platform/kernel/check-tool
        │
        ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│  HELIOS OS CLOUD RUN (forgeos-api — the control plane)                         │
│                                                                                │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  LAYER 3: FASTAPI ENDPOINT                                                │ │
│  │  (src/dashboard/fastapi_app.py:2084)                                      │ │
│  │                                                                           │ │
│  │  @app.post("/api/platform/kernel/check-tool")                             │ │
│  │  async def kernel_check_tool(req: ToolCheckRequest):                      │ │
│  │    k = _require_kernel()      # gets the platform Kernel singleton        │ │
│  │    decision = k.check_tool_call(                                          │ │
│  │      req.agent_id,            # "3cd5d08f-5f4"                            │ │
│  │      req.tool_name,           # "approve_discount"                        │ │
│  │      req.tool_input,          # {"value": 500}                            │ │
│  │      req.estimated_cost_usd,  # 0.01                                      │ │
│  │    )                                                                      │ │
│  │    return decision.to_dict()  # → JSON response                           │ │
│  └──────────────────────────────┬───────────────────────────────────────────┘ │
│                                 │                                              │
│  ┌──────────────────────────────▼───────────────────────────────────────────┐ │
│  │  LAYER 4: PLATFORM KERNEL (SAME AS MODE A)                                │ │
│  │  (src/platform/kernel/_facade.py:907)                                     │ │
│  │                                                                           │ │
│  │  def check_tool_call(self, agent_id, tool_name, ...):                     │ │
│  │    ① PermissionManager → allowed list check                               │ │
│  │    ② BudgetManager → spend limit check                                    │ │
│  │    ③ PolicyEngine → declarative rules                                     │ │
│  │    ④ Audit → hash-chained log                                             │ │
│  │    return KernelDecision(action="deny",                                   │ │
│  │      reason="Tool 'approve_discount' not in allowed tools")               │ │
│  └──────────────────────────────┬───────────────────────────────────────────┘ │
│                                 │                                              │
│                                 ▼                                              │
│  HTTP Response: 200 OK                                                         │
│  {                                                                             │
│    "action": "deny",                                                           │
│    "reason": "Tool 'approve_discount' not in agent's allowed tools",           │
│    "details": {"tool": "approve_discount", "allowed": ["memory__*", ...]},     │
│    "audit_id": "cc6dc80e-bf2",                                                 │
│    "timestamp": "2026-05-17T07:42:23Z"                                         │
│  }                                                                             │
└───────────────────────────────────────────────────────────────────────────────┘
        │
        │  HTTP Response back (~50ms total round-trip)
        │
        ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│  AGENT CLOUD RUN (continued)                                                   │
│                                                                                │
│  _HTTPBackend receives JSON → parses into KernelDecision                       │
│    decision = KernelDecision.from_dict(response_json)                          │
│    (src/forgeos_sdk/kernel.py:275)                                             │
│                                                                                │
│  runtime.check_tool() returns the KernelDecision to the caller                 │
│                                                                                │
│  if decision.denied:                                                           │
│    → Tool is NOT executed                                                      │
│    → Error returned to the LLM: "Kernel denied: not in allowed tools"          │
│    → LLM adapts (tries different tool or answers without it)                   │
│                                                                                │
│  if decision.allowed:                                                          │
│    → Tool executes normally                                                    │
│    → Result returned to LLM                                                    │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. The Backend Selection — How runtime Picks In-Process vs HTTP

```
src/forgeos_sdk/kernel.py

┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  class Kernel:                                                           │
│    _local_kernel: ClassVar = None   ← set by bootstrap                   │
│    _backend: _KernelBackend         ← the actual call path               │
│                                                                          │
│    ┌─────────────────────────────────────────────────────────────────┐   │
│    │  Kernel.local()  (line 101)                                     │   │
│    │    if cls._local_kernel:                                        │   │
│    │      return Kernel(_InProcessBackend(cls._local_kernel))        │   │
│    │    raise RuntimeError("No local kernel")                        │   │
│    └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│    ┌─────────────────────────────────────────────────────────────────┐   │
│    │  Kernel.remote(base_url, api_key)  (line 117)                   │   │
│    │    return Kernel(_HTTPBackend(base_url, api_key))                │   │
│    └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│    ┌─────────────────────────────────────────────────────────────────┐   │
│    │  Kernel.connect()  (line 122)  ← AUTO-DETECT                    │   │
│    │    try:                                                         │   │
│    │      return cls.local()         # try in-process first          │   │
│    │    except RuntimeError:                                         │   │
│    │      url = os.environ.get("FORGEOS_API_URL")                    │   │
│    │      key = os.environ.get("FORGEOS_API_KEY")                    │   │
│    │      return cls.remote(url, key)  # fall back to HTTP           │   │
│    └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│    ┌─────────────────────────────────────────────────────────────────┐   │
│    │  Kernel.register_local_instance(platform_kernel)  (line 131)    │   │
│    │    cls._local_kernel = platform_kernel                           │   │
│    │    # Called by bootstrap.py:257 at boot time                     │   │
│    │    # After this, Kernel.local() and Kernel.connect() return      │   │
│    │    # _InProcessBackend                                           │   │
│    └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘

BACKEND INTERFACE (both implement the same methods):

  ┌────────────────────────────┐    ┌────────────────────────────────────┐
  │  _InProcessBackend         │    │  _HTTPBackend                      │
  │  (line 204)                │    │  (line 232)                        │
  │                            │    │                                    │
  │  self._k = platform_kernel │    │  self._base_url = forgeos_url      │
  │                            │    │  self._http = httpx.AsyncClient    │
  │  check_tool_call():        │    │                                    │
  │    return self._k           │    │  check_tool_call():                │
  │      .check_tool_call(...)  │    │    return self._http.post(         │
  │    # DIRECT PYTHON CALL     │    │      "/api/platform/kernel/        │
  │    # ~0.1ms                 │    │       check-tool", json={...})     │
  │                            │    │    # HTTP ROUND-TRIP ~50ms         │
  │  check_a2a_call():         │    │                                    │
  │    return self._k           │    │  check_a2a_call():                 │
  │      .check_a2a_call(...)   │    │    return self._http.post(         │
  │                            │    │      "/api/platform/kernel/         │
  │  check_data_access():      │    │       check-a2a", json={...})      │
  │    return self._k           │    │                                    │
  │      .check_data_access()   │    │  record_usage():                   │
  │                            │    │    return self._http.post(          │
  │  audit():                  │    │      "/api/platform/kernel/         │
  │    self._k.audit(...)       │    │       usage", json={...})          │
  │                            │    │                                    │
  │  get_contract():           │    │  heartbeat():                      │
  │    return self._k           │    │    return self._http.post(         │
  │      .get_contract(...)     │    │      "/api/platform/agents/        │
  │                            │    │       {id}/heartbeat")              │
  └────────────────────────────┘    └────────────────────────────────────┘
```

---

## 5. Complete Call Chain — Side by Side

```
                    MODE A (In-Process)                    MODE C (HTTP)
                    ═══════════════════                    ══════════════

Agent code:         same                                   same
  │                                                         │
  ▼                                                         ▼

runtime.check_tool("approve_discount", {"value": 500})     runtime.check_tool("approve_discount", {"value": 500})
  │ runtime.py:264                                          │ runtime.py:264
  │                                                         │
  ▼                                                         ▼

self._kernel.check_tool_call(...)                          self._kernel.check_tool_call(...)
  │ kernel.py:141                                           │ kernel.py:141
  │                                                         │
  ▼                                                         ▼

_InProcessBackend.check_tool_call()                        _HTTPBackend.check_tool_call()
  │ kernel.py:210                                           │ kernel.py:265
  │                                                         │
  │ self._k.check_tool_call(...)                            │ httpx.post("/api/platform/
  │ DIRECT PYTHON CALL                                      │   kernel/check-tool", json={...})
  │                                                         │
  │                                                         │ ── NETWORK ──
  │                                                         │
  ▼                                                         ▼

                                                           FastAPI endpoint
                                                           fastapi_app.py:2084
                                                             │
                                                             ▼

Kernel.check_tool_call()                                   Kernel.check_tool_call()
  │ kernel/_facade.py:907                                   │ kernel/_facade.py:907
  │                                                         │
  │ SAME KERNEL CODE                                        │ SAME KERNEL CODE
  │ SAME CHECKS                                             │ SAME CHECKS
  │ SAME DECISION                                           │ SAME DECISION
  │                                                         │
  ▼                                                         ▼

  ① PermissionManager.check_tool_call()                    ① PermissionManager.check_tool_call()
     → Is tool in allowed list?                               → Is tool in allowed list?

  ② BudgetManager.check_budget()                           ② BudgetManager.check_budget()
     → Within daily/per-task limit?                           → Within daily/per-task limit?

  ③ PolicyEngine.evaluate()                                ③ PolicyEngine.evaluate()
     → Declarative policy rules                               → Declarative policy rules

  ④ Audit._audit()                                         ④ Audit._audit()
     → Hash-chained log entry                                 → Hash-chained log entry

  │                                                         │
  ▼                                                         ▼

KernelDecision(action="deny")                              KernelDecision(action="deny")
  │                                                         │
  │ returned directly as Python object                      │ serialized to JSON
  │                                                         │ sent as HTTP response
  │                                                         │ deserialized back to KernelDecision
  │                                                         │
  ▼                                                         ▼

decision.denied == True                                    decision.denied == True
  │                                                         │
  ▼                                                         ▼

Tool NOT executed                                          Tool NOT executed
Error returned to LLM                                      Error returned to LLM

LATENCY: ~0.1ms                                           LATENCY: ~50ms
```

---

## 6. All HTTP Endpoints the _HTTPBackend Calls

```
_HTTPBackend (src/forgeos_sdk/kernel.py:232)
  │
  ├── POST /api/platform/kernel/check-tool      → check_tool_call()
  │     Body: {agent_id, tool_name, tool_input, estimated_cost_usd}
  │     Response: {action, reason, details, audit_id, timestamp}
  │
  ├── POST /api/platform/kernel/check-a2a       → check_a2a_call()
  │     Body: {caller_agent_id, target_namespace, target_name}
  │     Response: {action, reason, ...}
  │
  ├── POST /api/platform/kernel/check-data      → check_data_access()
  │     Body: {agent_id, target_namespace}
  │     Response: {action, reason, ...}
  │
  ├── GET  /api/platform/kernel/contract/{id}   → get_contract()
  │     Response: full agent contract dict
  │
  ├── POST /api/platform/kernel/admit           → admit()
  │     Body: contract dict
  │     Response: {admitted, reason, errors, warnings}
  │
  ├── POST /api/platform/kernel/audit           → audit()
  │     Body: {agent_id, event, details}
  │     Response: {ok: true}
  │
  ├── POST /api/platform/kernel/usage           → record_usage()
  │     Body: {agent_id, tokens_in, tokens_out, cost_usd, tool_calls}
  │     Response: {recorded: true}
  │
  ├── POST /api/platform/agents/{id}/heartbeat  → heartbeat()
  │     Response: {ok: true}
  │
  ├── POST /api/platform/a2a/submit             → submit_task()
  │     Body: {caller_id, callee_namespace, callee_name, task, context}
  │     Response: {job_id, status}
  │
  ├── GET  /api/platform/a2a/jobs/{id}          → get_task_result()
  │     Response: task status + result
  │
  ├── POST /api/platform/a2a/result             → submit_result()
  │     Body: {job_id, result}
  │     Response: {ok: true}
  │
  └── GET  /api/platform/a2a/tasks/pending      → get_pending_tasks()
        Response: {tasks: [...]}


FastAPI receives these at (src/dashboard/fastapi_app.py):
  ├── check-tool:  line 2084
  ├── check-a2a:   line 2093
  ├── check-data:  line 2102
  ├── contract:    line 2109
  ├── admit:       line 2118
  ├── audit:       line 2125
  ├── usage:       line 2164
  ├── heartbeat:   line 2174
  ├── a2a/submit:  line 2180
  ├── a2a/jobs:    line 2198
  ├── a2a/result:  line 2205
  └── a2a/pending: line 2213
```

---

## 7. How bootstrap.py Wires Everything

```
src/bootstrap.py — PlatformBootstrap.boot()

① Create platform Kernel
   │  from src.platform.kernel import Kernel as PlatformKernel
   │  self._kernel = PlatformKernel(registry, tool_executor, ...)
   │                                  (line 238)
   │
② Register for in-process SDK access
   │  from src.forgeos_sdk.kernel import Kernel as SDKKernel
   │  SDKKernel.register_local_instance(self._kernel)
   │                                  (line 257)
   │  → Now Kernel.local() and Kernel.connect() return _InProcessBackend
   │
③ Register in runtime singleton
   │  from src.forgeos_sdk.runtime import runtime as sdk_runtime
   │  sdk_runtime.register_platform(
   │    kernel=self._kernel,
   │    process_table=self.executor.process_table,
   │    checkpoint_store=self.executor.checkpoint_store,
   │    a2h_gateway=self._a2h_gateway,
   │  )                               (line 272)
   │  → Now runtime.check_tool() calls self._kernel directly
   │
④ For each agent invoke():
   │  runtime.bind(agent_id, namespace)    (line 306)
   │  # ... agent runs, tools are gated ...
   │  runtime.unbind(token)                (line 367)
```

---

## 8. Developer Decision Tree

```
Q: Where does my agent run?

  ┌─── INSIDE Helios OS (deployed via manifest YAML)
  │
  │    → Mode A: In-process
  │    → runtime auto-wired by bootstrap
  │    → Kernel checks via _InProcessBackend (~0.1ms)
  │    → Zero code changes needed
  │    → Helios OS handles: bind, check_tool, unbind, record_usage
  │
  └─── OUTSIDE Helios OS (own Cloud Run / Lambda / K8s)

       Q: Do I want Helios OS governance?

       ├─── YES → Mode C: HTTP kernel
       │
       │    Add to your agent code (10 lines):
       │
       │    from forgeos_sdk.kernel import Kernel
       │    from forgeos_sdk.runtime import runtime
       │
       │    kernel = Kernel.connect()               # reads FORGEOS_API_URL
       │    runtime.register_platform(kernel=kernel)
       │    runtime.bind(AGENT_ID, namespace=NS)
       │
       │    # In your tool function:
       │    decision = await runtime.check_tool(name, input)
       │    if decision.denied: return "Blocked"
       │
       │    # After each invocation:
       │    await runtime.record_usage(tokens, cost)
       │    await runtime.heartbeat()
       │
       │    → All checks go via HTTP to Helios OS (~50ms each)
       │    → Same kernel, same rules, same audit trail
       │
       └─── NO → Mode B: Pure agent
            → No Helios OS SDK
            → No governance
            → Agent runs freely
```
