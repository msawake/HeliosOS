]]# SDK Developer Guide — One Agent Per Stack

Build a real agent on each ForgeOS stack, using every SDK runtime capability. Each section is self-contained — read only your framework's section to get the full picture.

**Quick links:** [ForgeOS](#1-forgeos--sales-pipeline-agent) | [CrewAI](#2-crewai--competitive-analyst) | [Google ADK](#3-google-adk--research-analyst) | [OpenClaw](#4-openclaw--compliance-monitor) | [Sandbox](#5-sandbox--data-processor)

---

## How the SDK Runtime Works (All Stacks)

Before diving into each stack, here's the universal pattern:

```python
from forgeos_sdk import runtime

# These work identically in ForgeOS, CrewAI, ADK, OpenClaw, and Sandbox.
# Identity is set automatically — you never pass agent_id.

await runtime.check_tool("email.send")         # Permission check
await runtime.budget()                          # Budget introspection
await runtime.reserve(0.05)                     # Two-phase reservation
await runtime.checkpoint({"step": 3})           # Save state
await runtime.request_capability(target="x")    # Scoped access token
await runtime.pending_signals()                 # Check for SIGTERM
await runtime.process()                         # Read own PID/phase/usage
await runtime.contract()                        # Read own manifest
await runtime.audit("event_name", {})           # Record custom event
await runtime.syscall("tool.call", target="x")  # Full pipeline admission
```

The executor calls `runtime.bind(agent_id, namespace)` before your agent code runs and `runtime.unbind()` after. You never see this — it just works.

---

## 1. ForgeOS — Sales Pipeline Agent

**Purpose:** Autonomous agent that qualifies leads, tracks budget, saves checkpoints at each pipeline stage, and handles shutdown signals gracefully.

### Manifest

```yaml
apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: sales-pipeline-agent
  namespace: sales
  labels:
    department: sales
    tier: "2"
spec:
  runtime:
    framework: forgeos
  lifecycle:
    type: autonomous
    restart_policy: on-failure
  llm:
    chat_model: claude-sonnet-4-5-20250514
    provider: anthropic
  capabilities:
    tools:
      allowed:
        - company__search_knowledge
        - company__record_metric
        - company__publish_event
        - company__add_decision
      denied:
        - company__request_approval    # must escalate, not self-approve
  boundaries:
    budgets:
      daily_usd: 5.00
      per_task_usd: 1.50
    data:
      allowed_namespaces: [sales, marketing]
      pii_policy: detect
  governance:
    audit_level: full
    policies:
      - name: no-shell
        deny_if: { op: contains, field: tool_name, value: shell }
```

### Where Kernel Enforcement Happens

```
executor.invoke()
  → runtime.bind(agent_id, "sales")          # identity set
  → ForgeOSAdapter.invoke()
    → run_agentic_loop()
      → LLM decides to call company__search_knowledge
      → _execute_tool("company__search_knowledge", {...})
        → runtime.check_tool("company__search_knowledge")   # KERNEL GATE
          → PermissionManager: in allowed list? YES
          → BudgetManager: within per-task limit? YES
          → PolicyEngine: no deny_if matches? YES
          → return KernelDecision(action="allow")
        → tool_executor.execute(...)                         # tool runs
      → result returned to LLM
  → runtime.unbind()
```

**File:** `src/platform/agentic_loop.py` line ~318 — the `_execute_tool()` function.

### SDK Runtime Usage in Agent Code

The system prompt instructs the LLM to use tools. Behind the scenes, every tool call passes through the kernel. But the agent can also use the runtime directly in custom tools:

```python
# Example: a custom tool that checks budget before expensive operations
async def qualify_lead_tool(lead_id: str, **kwargs):
    """Custom tool registered with the platform."""
    from forgeos_sdk import runtime

    # Check budget before proceeding
    budget = await runtime.budget()
    if budget.remaining_usd is not None and budget.remaining_usd < 0.50:
        await runtime.audit("budget_warning", {"remaining": budget.remaining_usd})
        return {"result": "Skipping — budget too low for qualification"}

    # Reserve budget for this operation
    ticket = await runtime.reserve(0.30)
    if ticket is None:
        return {"error": "Budget reservation denied"}

    try:
        # Do the actual work...
        score = compute_bant_score(lead_id)

        # Save checkpoint after scoring
        await runtime.checkpoint({
            "stage": "qualification",
            "lead_id": lead_id,
            "score": score,
            "budget_ticket": ticket,
        })

        # Commit actual cost
        await runtime.commit(ticket, actual_cost_usd=0.18)

        # Record metric
        return {"result": f"Lead {lead_id} scored {score}"}
    except Exception:
        # Release reservation on failure
        await runtime.release(ticket)
        raise
```

### Signal Handling in Autonomous Loops

The executor runs autonomous agents in a loop. At each iteration boundary, the agent can check for signals:

```python
# Inside the autonomous loop (managed by executor):
# 1. LLM runs → calls tools → kernel gates each one
# 2. After each iteration, executor saves a checkpoint
# 3. If admin sends SIGTERM:

signals = await runtime.pending_signals()
if "SIGTERM" in signals:
    # Save final state
    await runtime.checkpoint({
        "stage": "interrupted",
        "leads_qualified": 12,
        "resume_from": "lead-013",
    })
    await runtime.audit("graceful_shutdown", {"signal": "SIGTERM"})
    # Loop exits — agent can resume from checkpoint later
```

### Scaffold Files

```
agents/shared/sales-pipeline-agent/
  agent.py              # AgentDefinition with tools, budget, namespace
  tools.py              # TOOL_DEFINITIONS list
  prompts/system.md     # System prompt with pipeline instructions
  config.yaml           # LLM config, execution type, boundaries
```

### You Control vs Platform Controls

| You Control | Platform Controls |
|-------------|-------------------|
| System prompt (pipeline logic) | Permission checks per tool call |
| Which tools to declare | Budget enforcement (daily + per-task) |
| Budget limits in manifest | Checkpoint save/restore |
| Goal text for completion detection | Process table (PID, phase, usage) |
| Agent namespace | Audit trail of every decision |
| Restart policy | Signal delivery + handling |

---

## 2. CrewAI — Competitive Analyst

**Purpose:** A crew of one agent that researches competitors across multiple iterations, saves findings to the knowledge base, and tracks metrics. Demonstrates CrewAI's role/goal/backstory pattern with full kernel enforcement.

### Manifest

```yaml
apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: competitive-analyst
  namespace: marketing
  labels:
    crewai_role: "Senior Competitive Intelligence Analyst"
    crewai_goal: "Map competitor positioning and identify market gaps"
    crewai_backstory: "10 years in market research at McKinsey"
spec:
  runtime:
    framework: crewai
  lifecycle:
    type: autonomous
    max_iterations: 8
  llm:
    chat_model: claude-sonnet-4-5-20250514
  capabilities:
    tools:
      allowed:
        - company__search_knowledge
        - company__add_decision
        - company__record_metric
        - company__get_metric
  boundaries:
    budgets:
      daily_usd: 8.00
      per_task_usd: 2.00
    data:
      allowed_namespaces: [marketing, sales]
```

### Where Kernel Enforcement Happens

CrewAI runs in a worker thread via `Crew.kickoff()`. The kernel gate is inside each `BaseTool._run()` wrapper:

```
executor.invoke()
  → runtime.bind(agent_id, "marketing")
  → CrewAIAdapter.invoke()
    → _invoke_real()
      → crew.kickoff()                     # runs in executor thread
        → CrewAgent calls ForgeOSTool
          → BaseTool._run(**kwargs)
            → loop = asyncio.new_event_loop()
            → runtime.check_tool(tool_name)  # KERNEL GATE
              → denied? return "Error: Kernel denied: ..."
            → tool_executor.execute(...)     # tool runs
            → return str(result)
  → runtime.unbind()
```

**File:** `stacks/crewai/adapter.py` line ~84 — inside `ForgeOSTool._run()`.

**Why a new event loop?** CrewAI's `kickoff()` runs synchronously in a thread pool. That thread has no asyncio loop, so the wrapper creates one per tool call. This is safe because the thread is dedicated to that crew.

### CrewAI-Specific Code

```python
# What the adapter builds internally:

crew_agent = CrewAgent(
    role="Senior Competitive Intelligence Analyst",
    goal="Map competitor positioning and identify market gaps",
    backstory="10 years in market research at McKinsey",
    tools=[ForgeOSTool_search, ForgeOSTool_add_decision, ...],
    allow_delegation=False,
    verbose=True,
    llm="claude-sonnet-4-5-20250514",
)

task = Task(
    description=prompt,     # user's request
    agent=crew_agent,
    expected_output="Research findings",
)

crew = Crew(agents=[crew_agent], tasks=[task])
result = crew.kickoff()    # kernel gates every tool call inside
```

### SDK Runtime from Inside CrewAI Tools

Every ForgeOS tool wrapped as a `BaseTool` has access to the runtime:

```python
class CompetitorResearchTool(CrewBaseTool):
    name = "company__search_knowledge"
    description = "Search the knowledge base"

    def _run(self, query: str = "", **kwargs) -> str:
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            from forgeos_sdk import runtime

            # Check permissions (automatic via kernel gate, but you can also check manually)
            decision = loop.run_until_complete(runtime.check_tool(self.name))
            if decision.denied:
                return f"Error: {decision.reason}"

            # Check budget before expensive research
            budget = loop.run_until_complete(runtime.budget())
            if budget.remaining_usd and budget.remaining_usd < 1.0:
                return "Warning: budget running low, summarizing existing findings"

            # Execute the actual tool
            result = loop.run_until_complete(
                tool_executor.execute(self.name, {"query": query}, agent_context)
            )

            # Save progress checkpoint
            loop.run_until_complete(runtime.checkpoint({
                "last_query": query,
                "findings_count": len(result.get("results", [])),
            }))

            return str(result)
        finally:
            loop.close()
```

### Scaffold Files

```
agents/shared/competitive-analyst/
  agents.py     # Agent(role="Senior Competitive...", goal="Map competitor...")
  tasks.py      # Task(description=..., expected_output=...)
  crew.py       # Crew(agents=[...], tasks=[...], process='sequential')
  tools.py      # ForgeOS tools wrapped as CrewBaseTool
  config.yaml   # CrewAI config + ForgeOS boundaries
```

---

## 3. Google ADK — Research Analyst

**Purpose:** Enterprise research agent that investigates leads, coordinates with finance via capability tokens, and handles administrative signals. The most complete example — exercises all 9 SDK capabilities.

### Manifest

```yaml
apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: research-analyst
  namespace: sales
spec:
  runtime:
    framework: adk
  lifecycle:
    type: autonomous
    max_iterations: 12
  llm:
    chat_model: claude-sonnet-4-5-20250514
  capabilities:
    tools:
      allowed:
        - company__search_knowledge
        - company__publish_event
        - company__record_metric
        - company__add_decision
      denied:
        - company__request_approval
  boundaries:
    budgets:
      daily_usd: 5.00
      per_task_usd: 2.00
    data:
      allowed_namespaces: [sales, marketing]
  governance:
    audit_level: full
```

### Where Kernel Enforcement Happens

ADK tools are async `FunctionTool` wrappers. The kernel gate runs before execution:

```
executor.invoke()
  → runtime.bind(agent_id, "sales")
  → ADKAdapter.invoke()
    → _invoke_via_runner()
      → Runner.run_async(user_id, session_id, message)
        → LlmAgent decides to call a tool
        → FunctionTool async _wrapper(**kwargs)
          → runtime.check_tool(tool_name, kwargs)   # KERNEL GATE
            → denied? return {"success": False, "error": "Kernel denied: ..."}
          → tool_executor.execute(...)               # tool runs
          → return result dict
        → result fed back to LlmAgent
  → runtime.unbind()
```

**File:** `stacks/adk/adapter.py` line ~157 — inside `_wrapper()`.

### Complete SDK Usage (All 9 Capabilities)

This is the agent from `examples/adk/full_platform_adk_agent.py`. Inside its tool execution path, it demonstrates:

```python
from forgeos_sdk import runtime

# 1. PERMISSIONS — automatic on every tool call, but also checkable manually
decision = await runtime.check_tool("company__search_knowledge")
assert decision.allowed

decision = await runtime.check_tool("company__request_approval")
assert decision.denied  # in deny list

decision = await runtime.check_tool("shell.exec")
assert decision.denied  # not in allowed list

# 2. BUDGET — introspect, reserve, commit, release
budget = await runtime.budget()
# BudgetSnapshot(daily_limit_usd=5.0, per_task_usd=2.0, spent=0.55, reserved=0.0, remaining=4.45)

ticket = await runtime.reserve(0.80)          # hold $0.80
await runtime.commit(ticket, actual_cost_usd=0.55)  # actual was $0.55
# or: await runtime.release(ticket)           # abort path

# 3. CHECKPOINTS — save at logical boundaries, restore after crash
await runtime.checkpoint({
    "phase": "lead_scoring",
    "leads_scored": 3,
    "scores": {"Acme": 85, "GlobalTech": 72, "MegaInc": 91},
})
restored = await runtime.last_checkpoint()
# CheckpointData(extra={"phase": "lead_scoring", "leads_scored": 3, ...})

# 4. CAPABILITY TOKENS — scoped cross-namespace access
cap = await runtime.request_capability(
    target="finance/finance-approver",
    verb="a2a.invoke",
    ttl=600,  # 10 minutes
    metadata={"reason": "Budget approval for MegaInc campaign"},
)
# CapabilityToken(id="abc123...", subject=agent_id, target="finance/finance-approver")
await runtime.revoke_capability(cap.id)

# 5. SIGNALS — cooperative preemption
signals = await runtime.pending_signals()  # [] or ["SIGTERM"]
if "SIGTERM" in signals:
    await runtime.checkpoint({"interrupted": True})
    return  # graceful exit

# 6. A2A — cross-namespace permission checks
d = await runtime.check_a2a("sales", "data-enricher")      # allow (same ns)
d = await runtime.check_a2a("finance", "finance-approver")  # deny (cross ns)

# 7. PROCESS INTROSPECTION
proc = await runtime.process()
# ProcessSnapshot(pid="abc", phase="running", tokens_out=2400, dollars=0.55, tool_calls=6)

contract = await runtime.contract()
# {"name": "research-analyst", "stack": "adk", "tools": [...], ...}

# 8. SYSCALL — full 7-stage pipeline
d = await runtime.syscall("tool.call", target="company__search_knowledge")  # allow
d = await runtime.syscall("tool.call", target="shell.exec")                 # deny

# 9. AUDIT — custom events
await runtime.audit("research_complete", {
    "leads_analyzed": 3,
    "top_lead": "MegaInc",
    "budget_used": 0.55,
})
```

### Scaffold Files

```
agents/shared/research-analyst/
  agent.py              # LlmAgent(name, model, instruction, tools)
  workflow.py           # SequentialAgent / ParallelAgent (optional)
  tools.py              # FORGEOS_TOOL_WRAPPERS (injected at runtime)
  prompts/system_prompt.txt
  config.yaml           # checkpoints=true, workflow type, LLM config
  __init__.py
```

### Run the Demo

```bash
PYTHONPATH=. python3 examples/adk/full_platform_adk_agent.py
```

---

## 4. OpenClaw — Compliance Monitor

**Purpose:** Always-on agent that monitors policy compliance via heartbeat, publishes violations as events. Demonstrates the token-based tool proxy unique to OpenClaw.

### Manifest

```yaml
apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: compliance-monitor
  namespace: legal
spec:
  runtime:
    framework: openclaw
  lifecycle:
    type: always_on
    heartbeat_interval_seconds: 300
  llm:
    chat_model: claude-sonnet-4-5-20250514
  capabilities:
    tools:
      allowed:
        - company__search_knowledge
        - company__query_events
        - company__publish_event
        - company__record_metric
      denied:
        - company__add_decision    # compliance can report, not decide
  boundaries:
    budgets:
      daily_usd: 10.00
    data:
      allowed_namespaces: [legal, compliance]
      pii_policy: redact
```

### Where Kernel Enforcement Happens

OpenClaw is unique — the agent runs in a Node.js subprocess. Tool calls go through an HTTP proxy:

```
executor.invoke()
  → runtime.bind(agent_id, "legal")
  → OpenClawAdapter.invoke()
    → _ensure_gateway()
      → ToolProxyServer.start()           # HTTP server on :18790
      → OpenClawGateway.start()           # Node.js on :18789
    → _invoke_via_gateway()
      → gateway.chat(message, agent_id)
        → Node.js reads SOUL.md
        → LLM decides to call company__search_knowledge
        → Node.js reads SKILLS/default.yaml
        → POST http://127.0.0.1:18790/tool     # to ToolProxyServer
            Header: X-Agent-Token: sbx_abc123...
            Body: {"tool_name": "company__search_knowledge", "tool_input": {...}}
        → ToolProxyServer._process_tool_call()
          → SandboxTokenStore.verify(token)     # token valid?
          → runtime.bind(agent_id, "legal")     # bind for this call
          → runtime.check_tool(tool_name)       # KERNEL GATE
            → denied? return {"error": "Kernel denied: ..."}
          → tool_executor.execute(...)          # tool runs
          → runtime.unbind()
          → return JSON result
        → Node.js receives result → feeds to LLM
  → runtime.unbind()
```

**File:** `stacks/openclaw/adapter.py` — `ToolProxyServer._process_tool_call()`.

### The Token Flow

When you deploy an OpenClaw agent, the adapter mints a scoped token:

```python
# Automatic at create_agent():
from stacks.sandbox.adapter import get_token_store

token = get_token_store().mint(agent_def)
# token = "sbx_a3b2c1d4e5f6..."
# claims = {
#     "agent_id": "abc-123",
#     "namespace": "legal",
#     "tools": ["company__search_knowledge", ...],
#     "tier": 3,
#     "created_at": 1713765600,
# }
```

The token is written into:
- `SOUL.md` — so the LLM knows how to call tools
- `SKILLS/default.yaml` — so the gateway includes it in HTTP requests

### Your Workspace Files

```
~/.openclaw-forgeos/workspaces/compliance-monitor/
  SOUL.md                   # Agent personality + tool proxy instructions
  AGENTS.md                 # Metadata: department, execution type
  HEARTBEAT.md              # 300s interval, compliance check cycle
  SKILLS/default.yaml       # Tool endpoints pointing to ToolProxyServer
  MEMORY/long-term.md       # Persistent memory across sessions
```

**SOUL.md** (auto-generated with proxy instructions):

```markdown
# SOUL

You are compliance-monitor — an autonomous OpenClaw agent.

Goal: Monitor policy compliance and report violations.

## Rules
- Think step by step using ReAct: Think → Act → Observe → Repeat
- Never guess — confirm before external actions
- Log decisions to memory
- Respect rate limits and budgets

## Tool Usage
To call a tool, POST to http://127.0.0.1:18790/tool with:
  {"tool_name": "<name>", "tool_input": {...}}
  Header: X-Agent-Token: sbx_a3b2c1d4e5f6...
All tool calls are validated by the ForgeOS kernel.
```

**SKILLS/default.yaml** (auto-generated):

```yaml
- name: company__search_knowledge
  trigger: "use company__search_knowledge"
  description: "Calls company__search_knowledge via ForgeOS kernel proxy"
  method: POST
  endpoint: "http://127.0.0.1:18790/tool"
  headers:
    X-Agent-Token: "sbx_a3b2c1d4e5f6..."
  body:
    tool_name: "company__search_knowledge"
    tool_input: "{{params}}"

- name: company__publish_event
  trigger: "use company__publish_event"
  description: "Calls company__publish_event via ForgeOS kernel proxy"
  method: POST
  endpoint: "http://127.0.0.1:18790/tool"
  headers:
    X-Agent-Token: "sbx_a3b2c1d4e5f6..."
  body:
    tool_name: "company__publish_event"
    tool_input: "{{params}}"
```

### What Gets Denied

```
Agent tries: company__add_decision
  → POST /tool with tool_name="company__add_decision"
  → ToolProxyServer verifies token ✓
  → runtime.check_tool("company__add_decision")
  → PermissionManager: "company__add_decision" is in denied list
  → return {"error": "Kernel denied: Tool 'company__add_decision' is explicitly denied"}
  → Node.js receives error → LLM sees: "Error: Kernel denied: ..."
  → LLM adjusts behavior (reports finding instead of recording decision)
```

### You Control vs Platform Controls

| You Control | Platform Controls |
|-------------|-------------------|
| `SOUL.md` — personality + reasoning style | Token minting + validation |
| `AGENTS.md` — metadata | `SKILLS/` proxy endpoints (auto-generated) |
| `HEARTBEAT.md` — schedule | Kernel permission checks per tool call |
| `MEMORY/` — persistent memory | Budget enforcement |
| Which tools to list in manifest | Audit trail |
| ReAct patterns in SOUL | Process table tracking |

---

## 5. Sandbox — Data Processor

**Purpose:** Reflex agent that runs in a Docker container with resource limits and network isolation. Demonstrates the most secure execution model — every tool call proxied through the ForgeOS API with token authentication.

### Manifest

```yaml
apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: data-processor
  namespace: analytics
  labels:
    tier: "3"     # tier >= 3 → auto-routed to sandbox
spec:
  runtime:
    framework: sandbox
    image: forgeos-sandbox:latest
    mem_limit: 256m
    cpu_quota: 50000
  lifecycle:
    type: reflex
  llm:
    chat_model: gpt-4o-mini
    provider: openai
  capabilities:
    tools:
      allowed:
        - company__search_knowledge
        - company__record_metric
  boundaries:
    budgets:
      daily_usd: 2.00
      per_task_usd: 0.50
    data:
      allowed_namespaces: [analytics]
```

### Where Kernel Enforcement Happens

The agent runs inside a Docker container. It has NO direct access to the kernel — everything goes through HTTP:

```
executor.invoke()
  → SandboxAdapter.invoke()
    → _token_store.mint(agent_def)          # scoped token
    → docker.containers.run(
        image="forgeos-sandbox:latest",
        environment={
            "AGENT_ID": agent_id,
            "AGENT_TOKEN": "sbx_xyz...",
            "FORGEOS_API_URL": "http://host:5000",
            "AGENT_MODEL": "gpt-4o-mini",
            "AGENT_SYSTEM_PROMPT": "...",
            "AGENT_TOOLS": '["company__search_knowledge", ...]',
            "AGENT_PROMPT": user_prompt,
        },
        mem_limit="256m",
        network="forgeos-internal",
      )
    → Inside container: SandboxRunner.run()
      → GET /api/platform/tools              # fetch tool schemas
      → LLM loop:
        → LLM decides to call company__search_knowledge
        → POST /api/sandbox/tool             # KERNEL GATE
            Header: X-Agent-Token: sbx_xyz...
            Body: {"tool_name": "company__search_knowledge", "tool_input": {...}}
          → FastAPI endpoint:
            → SandboxTokenStore.verify(token)
            → Kernel permission check
            → tool_executor.execute(...)
            → return result
        → result fed back to LLM
      → stdout: {"status": "completed", "output": "..."}
    → SandboxAdapter parses container output
```

**Files:**
- `stacks/sandbox/adapter.py` — container lifecycle, token minting
- `src/forgeos_sandbox/runner.py` — the agent loop running inside the container
- `src/dashboard/fastapi_app.py` line ~2054 — `/api/sandbox/tool` endpoint

### The Container Runner

Inside the Docker container, `runner.py` manages the agentic loop:

```python
# src/forgeos_sandbox/runner.py (runs inside container)

class SandboxRunner:
    def __init__(self):
        self.agent_id = os.environ["AGENT_ID"]
        self.token = os.environ["AGENT_TOKEN"]
        self.api_url = os.environ["FORGEOS_API_URL"]
        self._http = httpx.Client(
            base_url=self.api_url,
            headers={"X-Agent-Token": self.token},
        )

    def _proxy_tool(self, tool_name, tool_input):
        """Every tool call goes through the API — kernel validates."""
        resp = self._http.post("/api/sandbox/tool", json={
            "tool_name": tool_name,
            "tool_input": tool_input,
        })
        return resp.json()

    def _build_tool_schemas(self):
        """Fetch available tool schemas from the platform."""
        resp = self._http.get("/api/platform/tools")
        all_tools = resp.json()
        return [t for t in all_tools if t["name"] in self.allowed_tools]
```

### Security Properties

| Property | How it's enforced |
|----------|-------------------|
| **Process isolation** | Docker container with `mem_limit` and `cpu_quota` |
| **Network isolation** | `forgeos-internal` Docker network |
| **Tool authorization** | Token-scoped to agent's tool list |
| **Token expiry** | 24-hour TTL on `SandboxTokenStore` tokens |
| **Token revocation** | `_token_store.revoke(agent_id)` on stop |
| **Kernel enforcement** | `/api/sandbox/tool` validates via kernel before execution |
| **Resource limits** | Container killed if exceeding memory/CPU |

### Scaffold Files

Minimal — most config lives as Docker environment variables:

```
agents/shared/data-processor/
  sandbox.json    # {"agent_id": "...", "image": "forgeos-sandbox:latest", "mem": "256m"}
```

### You Control vs Platform Controls

| You Control | Platform Controls |
|-------------|-------------------|
| System prompt | Docker container lifecycle |
| Which tools to declare | Resource limits (mem, CPU) |
| Model selection | Token minting + validation |
| Budget limits | Network isolation |
| | Kernel permission checks (via API proxy) |
| | Container restart policy |
| | Token expiry + revocation on stop |

---

## Quick Comparison

| | ForgeOS | CrewAI | ADK | OpenClaw | Sandbox |
|---|---------|--------|-----|----------|---------|
| **Runtime** | Native agentic loop | Crew.kickoff() | Runner.run_async() | Node.js gateway | Docker container |
| **Kernel gate location** | `_execute_tool()` | `BaseTool._run()` | `FunctionTool._wrapper()` | `ToolProxyServer` | `/api/sandbox/tool` |
| **Gate mechanism** | In-process | In-process (sync) | In-process (async) | HTTP + token | HTTP + token |
| **Identity** | contextvars | contextvars | contextvars | token claims | env vars |
| **Files you write** | agent.py, prompts/ | agents.py, tasks.py, crew.py | agent.py, workflow.py | SOUL.md, HEARTBEAT.md | sandbox.json |
| **Isolation** | Process-level | Thread-level | Process-level | Subprocess | Container |
| **Best for** | Full control | Role-based collaboration | Enterprise workflows | Markdown-driven | Untrusted code |

---

## Running the Demos

```bash
# ForgeOS + all capabilities (no API keys needed)
PYTHONPATH=. python3 examples/full_platform_demo.py

# ADK + all capabilities (no API keys needed)
PYTHONPATH=. python3 examples/adk/full_platform_adk_agent.py

# All hello-world agents (requires running platform)
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
# In another terminal:
PYTHONPATH=. python3 examples/run_all_hello_world.py
```
