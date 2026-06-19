# Helios OS Governance Code Trace

Exactly where the kernel runs for each deployed agent — file paths, line numbers, and complete request traces.

## Deployed Agents

| Agent | ID | Namespace | Type | Manifest |
|-------|----|-----------|------|----------|
| customer-service | `3cd5d08f-5f4` | support | Single agent | `examples/adk-agents/customer-service.yaml` |
| marketing-coordinator | `e8151c82-2af` | marketing | Team supervisor (5 agents) | `examples/adk-agents/marketing-agency.yaml` |
| financial-coordinator | `d6414e4a-d2c` | finance | Team supervisor (5 agents) | `examples/adk-agents/financial-advisor.yaml` |

---

## The 5 Governance Points

Every agent invocation passes through these points in order:

```
① Runtime Bind → ② Kernel Tool Gate → ③ Usage Accounting → ④ A2A ACL → ⑤ Team Wiring
```

---

### ① Runtime Bind

**File:** `src/platform/executor.py` lines 302–313  
**When:** Start of every invocation  
**What:** Sets a context variable so all code in this async task knows which agent is executing. The kernel reads this to look up permissions and budgets.

```python
# src/platform/executor.py:302-313

# Bind the SDK runtime so agent code can use
# ``from forgeos_sdk import runtime`` without passing agent_id.
_rt_token = None
try:
    from src.forgeos_sdk.runtime import runtime as _sdk_runtime
    if _sdk_runtime.is_registered:
        _rt_token = _sdk_runtime.bind(
            agent_id,                                              # "3cd5d08f-5f4"
            namespace=getattr(agent_def, "namespace", "default"),  # "support"
        )
except Exception:
    pass
```

**Cleanup** at `src/platform/executor.py:367-371`:

```python
finally:
    if _rt_token is not None:
        try:
            _sdk_runtime.unbind(_rt_token)
        except Exception:
            pass
```

**Per agent:**
| Agent | bind() call |
|-------|------------|
| customer-service | `bind("3cd5d08f-5f4", namespace="support")` |
| marketing-coordinator | `bind("e8151c82-2af", namespace="marketing")` |
| financial-coordinator | `bind("d6414e4a-d2c", namespace="finance")` |

---

### ② Kernel Tool Gate

**File:** `src/platform/agentic_loop.py` lines 428–438  
**When:** Before EVERY tool call. The LLM decides to call a tool; the kernel checks permission first.  
**What:** Calls `runtime.check_tool()` → `kernel.check_tool_call()` → runs PermissionManager + BudgetManager + PolicyEngine.

```python
# src/platform/agentic_loop.py:428-438

# Kernel gate: check permissions + budget before executing the tool.
try:
    from src.forgeos_sdk.runtime import runtime as _rt
    if _rt.is_registered and _rt.is_bound:
        decision = await _rt.check_tool(tool_name, tool_input)
        if decision.denied:
            return {"error": f"Kernel denied: {decision.reason}"}
        if hasattr(decision, "action") and decision.action == "rate_limit":
            return {"error": f"Rate limited: {decision.reason}"}
except Exception:
    pass
```

**What the kernel checks** (inside `kernel.check_tool_call()` at `src/platform/kernel/_facade.py:907`):

1. **PermissionManager** (`_facade.py:261`) — Is the tool in the agent's allowed list?
2. **BudgetManager** (`_facade.py:363`) — Has the agent exceeded its daily/per-task budget?
3. **PolicyEngine** (`_facade.py:556`) — Do any declarative policies deny this?

**Per agent — examples:**

| Agent | Tool | Result | Why |
|-------|------|--------|-----|
| customer-service | `send_care_instructions` | ✅ ALLOW | In allowed list |
| customer-service | `approve_discount` | ❌ DENY | Not in allowed list (in `denied` in manifest) |
| customer-service | `memory__read` | ✅ ALLOW | Matches wildcard `memory__*` |
| customer-service | `update_salesforce_crm` | ❌ DENY | Explicitly in denied tools |
| marketing-coordinator | `agent__call` | ✅ ALLOW | In allowed list (then A2A check follows) |
| financial-coordinator | `agent__call` | ✅ ALLOW | In allowed list |
| domain-creator | `agent__call` | ❌ DENY | Not in worker's allowed list |

---

### ③ Usage Accounting

**File:** `src/platform/executor.py` lines 322–328  
**When:** After every invocation completes  
**What:** Records tokens, tool calls, and wallclock time on the process table. The BudgetManager reads these cumulative numbers on the next check.

```python
# src/platform/executor.py:322-328

# Record runtime accounting on the process.
self.process_table.record_usage(
    agent_id,
    tokens_out=result.tokens_used or 0,        # e.g. 6019
    tool_calls=len(result.tool_calls or []),    # e.g. 2
    wallclock_ms=result.elapsed_ms or 0.0,     # e.g. 3400.0
)
self.process_table.heartbeat(agent_id)
```

**Where the data lives:** `src/platform/kernel/_process.py:94` (`ResourceUsage` dataclass)

```python
@dataclass
class ResourceUsage:
    tokens_in: int = 0
    tokens_out: int = 0
    dollars: float = 0.0
    tool_calls: int = 0
    wallclock_ms: float = 0.0
    last_heartbeat_at: str | None = None
```

**Visible via:** `GET /api/platform/fleet` → shows per-agent spend, tokens, tool calls, last heartbeat.

---

### ④ A2A ACL Enforcement

**File:** `src/platform/kernel/_facade.py` lines 946–965  
**When:** When a coordinator calls a sub-agent via `agent__call`  
**What:** Checks both the caller's `canCall` list and the callee's `canBeCalledBy` list.

```python
# src/platform/kernel/_facade.py:946-965

def check_a2a_call(self, caller_agent_id, target_namespace, target_name):
    # 1. Look up caller in registry
    caller_def = self._registry.get(caller_agent_id)
    
    # 2. Read caller's capabilities.a2a.canCall
    capabilities = read_v2_section(caller_def, "capabilities", {})
    a2a_config = capabilities.get("a2a", {})
    can_call = a2a_config.get("canCall", [])
    
    # 3. Check if target is in caller's canCall list
    # ... (namespace + name matching with wildcard support)
    
    # 4. Look up callee and check canBeCalledBy
    # ... (reverse check)
    
    # 5. Return allow or deny
```

**Per agent — what happens:**

**marketing-coordinator → domain-creator:**
```
Caller canCall: [{namespace: "marketing", agents: ["domain-creator", "website-creator", 
                  "marketing-strategist", "logo-creator"]}]
→ "domain-creator" found → PASS

Callee canBeCalledBy: [{namespace: "marketing", agents: ["marketing-coordinator"]}]
→ "marketing-coordinator" found → PASS

Result: KernelDecision(action="allow")
```

**marketing-coordinator → finance/data-analyst:**
```
Caller canCall: [{namespace: "marketing", agents: [...]}]
→ "data-analyst" NOT in marketing namespace agents → DENY

Result: KernelDecision(action="deny", reason="cross-namespace call not permitted")
```

---

### ⑤ Team A2A Wiring

**File:** `src/platform/executor.py` lines 551–588  
**When:** At deploy time only (not at invocation time)  
**What:** Reads the team manifest's `orchestration` pattern and automatically sets `canCall`/`canBeCalledBy` ACLs on each agent.

```python
# src/platform/executor.py:551-588

def _wire_team_a2a(self, team_manifest, agent_spec, agent_def, all_agent_names, index):
    namespace = team_manifest.metadata.namespace
    orchestration = team_manifest.spec.orchestration
    other_names = [n for n in all_agent_names if n != agent_spec.name]

    if orchestration == "supervisor":
        if agent_spec.role == "supervisor":
            # Supervisor can call ALL workers
            a2a["canCall"] = [{"namespace": namespace, "agents": other_names}]
        else:
            # Workers can ONLY be called by supervisor
            supervisors = [a.name for a in team_manifest.spec.agents if a.role == "supervisor"]
            a2a["canBeCalledBy"] = [{"namespace": namespace, "agents": supervisors}]

    elif orchestration == "sequential":
        # Chain: each can only call the next
        if index < len(all_agent_names) - 1:
            a2a["canCall"] = [{"namespace": namespace, "agents": [all_agent_names[index + 1]]}]
        if index > 0:
            a2a["canBeCalledBy"] = [{"namespace": namespace, "agents": [all_agent_names[index - 1]]}]

    elif orchestration == "mesh":
        # Everyone can call everyone
        a2a["canCall"] = [{"namespace": namespace, "agents": other_names}]
        a2a["canBeCalledBy"] = [{"namespace": namespace, "agents": other_names}]
```

**Result for marketing-agency (supervisor):**

| Agent | canCall | canBeCalledBy |
|-------|--------|---------------|
| marketing-coordinator | domain-creator, website-creator, marketing-strategist, logo-creator | — |
| domain-creator | — | marketing-coordinator |
| website-creator | — | marketing-coordinator |
| marketing-strategist | — | marketing-coordinator |
| logo-creator | — | marketing-coordinator |

**Result for financial-advisor (supervisor):**

| Agent | canCall | canBeCalledBy |
|-------|--------|---------------|
| financial-coordinator | data-analyst, trading-analyst, execution-analyst, risk-analyst | — |
| data-analyst | — | financial-coordinator |
| trading-analyst | — | financial-coordinator |
| execution-analyst | — | financial-coordinator |
| risk-analyst | — | financial-coordinator |

---

## Complete Request Trace: Customer Service (Single Agent)

```
User types "help me with my plant" in browser

1. Browser → Dashboard (Next.js)
   POST /api/platform/agents/3cd5d08f-5f4/chat/stream
   Body: {"message": "help me with my plant", "session_id": "sess-abc"}

2. Dashboard proxy (next.config.js:23-27)
   → Rewrites to: https://forgeos-api.example.com/api/platform/agents/3cd5d08f-5f4/chat/stream

3. FastAPI endpoint (fastapi_app.py:774)
   → agent_chat_stream() receives request
   → Calls platform_executor.invoke("3cd5d08f-5f4", message, session_id="sess-abc")

4. PlatformExecutor.invoke() (executor.py:275)
   → Looks up agent "3cd5d08f-5f4" in registry
   → Gets Helios OS adapter
   → Loads session history for "sess-abc"

5. ★ POINT ①: Runtime bind (executor.py:306-311)
   → runtime.bind("3cd5d08f-5f4", namespace="support")
   → All subsequent code in this async task knows: agent_id="3cd5d08f-5f4"

6. Adapter invoke (stacks/forgeos/adapter.py)
   → Calls run_agentic_loop()

7. Agentic loop (agentic_loop.py:92)
   → Builds tool definitions from agent's allowed list
   → Sends system_prompt + history + user message to LLM

8. LLM call (llm_router.py:589 → _call_vertex)
   → Gemini 2.5 Flash via Vertex AI
   → Returns: tool_calls = [{"name": "send_care_instructions", "input": {...}}]

9. ★ POINT ②: Kernel tool gate (agentic_loop.py:428-438)
   → runtime.check_tool("send_care_instructions", {...})
   → kernel.check_tool_call("3cd5d08f-5f4", "send_care_instructions", {...})
     → PermissionManager (_facade.py:261): in allowed list? → YES
     → BudgetManager (_facade.py:363): within $5/day? → YES ($0.08 spent)
   → KernelDecision(action="allow")

10. Tool executes (tool_executor.py)
    → Returns care instructions data

11. Tool result → LLM → final response generated

12. ★ POINT ③: Usage accounting (executor.py:322-328)
    → process_table.record_usage(tokens_out=6019, tool_calls=2)
    → process_table.heartbeat("3cd5d08f-5f4")

13. ★ POINT ① cleanup: Runtime unbind (executor.py:367-371)
    → runtime.unbind(token)

14. Response streamed via SSE (fastapi_app.py:862)
    → data: {"type": "text_delta", "content": "Here are care instructions..."}
    → data: {"type": "done", "tokens_used": 6019}
```

---

## Complete Request Trace: Marketing Coordinator (Multi-Agent)

```
User types "Create online presence for PawPerfect" in browser

1-7. Same as customer-service, but:
     agent_id = "e8151c82-2af", namespace = "marketing"
     System prompt: "You are a marketing coordinator..."

8. LLM responds:
   tool_calls = [{"name": "agent__call", "input": {
     "namespace": "marketing", "name": "domain-creator",
     "task": "Find domain names for PawPerfect pet grooming"
   }}]

9. ★ POINT ②: Kernel tool gate (agentic_loop.py:428-438)
   → check_tool("agent__call", {...})
   → KernelDecision(action="allow") — agent__call is in coordinator's tools

10. Tool executor routes to A2A handler (tool_executor.py:49)
    → _handle_a2a_call()
    → A2AHandler.call() (a2a.py:165)

11. ★ POINT ④: A2A ACL check (kernel/_facade.py:946-965)
    → kernel.check_a2a_call("e8151c82-2af", "marketing", "domain-creator")
    → Caller canCall includes "domain-creator" → PASS
    → Callee canBeCalledBy includes "marketing-coordinator" → PASS
    → KernelDecision(action="allow")

12. A2A handler invokes domain-creator:
    → executor.invoke("0c111991-d78", "Find domain names...")

    12a. ★ POINT ①: runtime.bind("0c111991-d78", namespace="marketing")
    12b. domain-creator's agentic loop runs
    12c. ★ POINT ②: kernel gates domain-creator's memory__write tool
    12d. ★ POINT ③: usage recorded for domain-creator
    12e. ★ POINT ① cleanup: runtime.unbind()
    12f. Returns: "Suggested domains: pawfect.com, austinpaws.io..."

13. Result returns to coordinator's agentic loop
    → Coordinator's LLM sees the result
    → Calls next agent: agent__call("marketing", "website-creator", ...)
    → Steps 9-12 repeat for website-creator

14. Same for marketing-strategist and logo-creator
    → Each sub-agent gets its own:
      - Runtime bind (POINT ①)
      - Kernel tool checks (POINT ②)
      - Usage accounting (POINT ③)
      - A2A ACL verification (POINT ④)

15. After all 4 sub-agents complete:
    → Coordinator synthesizes results
    → Final response: "Here's your complete marketing package..."

16. ★ POINT ③: Usage accounting for coordinator
    → tokens_out=23037, tool_calls=6

Total kernel checks for this one request:
  - 1 runtime bind for coordinator
  - 4 runtime binds for sub-agents (1 each)
  - ~6 tool gate checks (agent__call × 4 + memory tools)
  - 4 A2A ACL checks
  - 5 usage recordings (1 coordinator + 4 sub-agents)
  = ~20 kernel interactions, each ~0.1ms = ~2ms total governance overhead
```

---

## Remote Governance Endpoints

When agents run OUTSIDE Helios OS (separate Cloud Run service), they call these HTTP endpoints instead:

| Endpoint | File:Line | In-Process Equivalent |
|----------|-----------|----------------------|
| `POST /api/platform/kernel/check-tool` | `fastapi_app.py:2084` | `kernel.check_tool_call()` |
| `POST /api/platform/kernel/check-a2a` | `fastapi_app.py:2093` | `kernel.check_a2a_call()` |
| `POST /api/platform/kernel/check-data` | `fastapi_app.py:2102` | `kernel.check_data_access()` |
| `POST /api/platform/kernel/usage` | `fastapi_app.py:2164` | `process_table.record_usage()` |
| `POST /api/platform/kernel/audit` | `fastapi_app.py:2125` | `kernel.audit()` |
| `POST /api/platform/agents/{id}/heartbeat` | `fastapi_app.py:2174` | `process_table.heartbeat()` |
| `POST /api/platform/a2a/submit` | `fastapi_app.py:2180` | `A2AHandler.async_call()` |
| `GET /api/platform/a2a/jobs/{id}` | `fastapi_app.py:2198` | `A2AHandler.get_result()` |
| `GET /api/platform/fleet` | `fastapi_app.py:2218` | `process_table.ps()` |

**Latency difference:**
- In-process: ~0.1ms per kernel check (direct Python function call)
- Remote HTTP: ~50-100ms per kernel check (network round-trip to Cloud Run)

---

## File Index

| File | Role | Key Lines |
|------|------|-----------|
| `src/platform/executor.py` | Agent lifecycle, runtime bind, team deploy | 302-313 (bind), 322-328 (usage), 367-371 (unbind), 425-462 (deploy_team), 551-588 (wire_a2a) |
| `src/platform/agentic_loop.py` | LLM tool-use loop, kernel gate | 428-438 (check before every tool) |
| `src/platform/kernel/_facade.py` | Kernel policy engine | 254-362 (PermissionManager), 363-555 (BudgetManager), 556-655 (PolicyEngine), 663-705 (DataBoundaryManager), 907-945 (check_tool_call), 946-965 (check_a2a_call) |
| `src/platform/kernel/_syscall.py` | Syscall pipeline (ordered stages) | 104-180 (SyscallPipeline.run), 187-337 (stage factories) |
| `src/platform/kernel/_process.py` | Process table, phase machine | 94-128 (ResourceUsage), 284-443 (ProcessTable), 338-351 (transition + cascade) |
| `src/platform/kernel/_capabilities.py` | Capability tokens | 60-106 (CapabilityToken), 151-232 (CapabilityManager) |
| `src/platform/kernel/_checkpoint.py` | Checkpoint/restore | 89-144 (Checkpoint), 167-195 (MemoryCheckpointStore) |
| `src/forgeos_sdk/runtime.py` | Agent-side kernel proxy | bind(), unbind(), check_tool(), is_registered, is_bound |
| `src/forgeos_sdk/kernel.py` | Kernel backend selection | 204-230 (_InProcessBackend), 232-330 (_HTTPBackend) |
| `src/platform/a2a.py` | Agent-to-agent calls | 165-320 (call with ACL, delegation, cycle detection) |
| `forgeos_mcp/integration/tool_executor.py` (forgeos-mcp repo) | Tool routing | 49-82 (handler registration), 265+ (execute) |
| `src/dashboard/fastapi_app.py` | API endpoints | 774 (chat/stream), 2084-2130 (kernel), 2164-2235 (remote governance) |
| `src/platform/namespace_policy.py` | Namespace-level governance | validate_agent(), is_tool_allowed() |
| `src/platform/fleet_monitor.py` | Auto-quarantine, alerts | check_fleet(), _check_error_rate() |
| `src/platform/rbac.py` | Platform RBAC | check(), require(), Role enum |
| `src/platform/task_queue.py` | Distributed A2A queue | submit(), claim(), submit_result() |
| `src/platform/memory_store.py` | Agent memory with concurrency | read(), write() with precondition hash |
