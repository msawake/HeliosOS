# ForgeOS Stack Adapters — Complete Guide

ForgeOS governs agents across 8 different frameworks. Each adapter wraps the framework's standard tool interface with kernel governance. This document explains how each adapter works, what it intercepts, and how to use it.

---

## The 8 Adapters

| # | Adapter | Stack Name | Framework | Governance Hook | File |
|---|---------|-----------|-----------|----------------|------|
| 1 | ForgeOS Native | `forgeos` | ForgeOS agentic loop | `runtime.check_tool()` in loop | `stacks/forgeos/adapter.py` |
| 2 | CrewAI | `crewai` | CrewAI SDK (Crew.kickoff) | `BaseTool._run()` override | `stacks/crewai/adapter.py` |
| 3 | Google ADK | `adk` | Google ADK Runner | `FunctionTool` wrapper | `stacks/adk/adapter.py` |
| 4 | OpenClaw | `openclaw` | HTTP gateway subprocess | Tool proxy server | `stacks/openclaw/adapter.py` |
| 5 | Sandbox | `sandbox` | Docker container | HTTP proxy | `stacks/sandbox/adapter.py` |
| 6 | **Anthropic Agent SDK** | `anthropic-agent-sdk` | Claude Agent SDK (`query()`) | **`PreToolUse` hook** | `stacks/anthropic_agent/adapter.py` |
| 7 | **Anthropic Managed** | `anthropic-managed` | Anthropic hosted sandbox | **Session-level gate** | `stacks/anthropic_managed/adapter.py` |
| 8 | **OpenAI Agents** | `openai-agents` | OpenAI Agents SDK + Responses API | **`AgentHooks.on_tool_start()`** | `stacks/openai_agents/adapter.py` |

---

## Anthropic Agent SDK Adapter (`anthropic-agent-sdk`)

**File:** `stacks/anthropic_agent/adapter.py` (~300 lines)

### What it does

When `claude-agent-sdk` is installed, this adapter runs real Claude agents using the official SDK. ForgeOS tools are exposed as an in-process MCP server, and a single `PreToolUse` hook gates ALL tool calls through the kernel.

### How it works

```
Agent code → Claude SDK query() → LLM decides to call tool
  → SDK fires PreToolUse hook
    → _forgeos_kernel_hook() → runtime.check_tool()
      → In-process: kernel.check_tool_call() (~0.1ms)
      → HTTP mode: POST /kernel/check-tool (~50ms)
    → If DENY: return {"permissionDecision": "deny"} → tool skipped
    → If ALLOW: tool executes via MCP server → result back to LLM
```

### Key advantage

**ONE hook gates ALL tools.** Unlike ADK (wrapper per tool) or CrewAI (subclass per tool), the Anthropic SDK's `PreToolUse` hook is a single function that intercepts every tool call. Zero per-tool code.

### Three modes

| Mode | Where agent runs | Kernel transport | Hook function |
|------|-----------------|-----------------|---------------|
| **A** (in-process) | Inside ForgeOS | Direct Python call | `_forgeos_kernel_hook()` |
| **B** (pure) | Own Cloud Run | None | No governance |
| **C** (remote) | Own Cloud Run | HTTP to ForgeOS | `make_remote_kernel_hook(url, agent_id)` |

### SDK detection

```python
try:
    from claude_agent_sdk import query, ClaudeAgentOptions, HookMatcher
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False  # falls back to ForgeOS agentic loop
```

### Tool bridging

ForgeOS tools are wrapped as an in-process MCP server that the SDK connects to:

```python
mcp_server = create_sdk_mcp_server(
    name="forgeos", version="1.0.0",
    tools=[...ForgeOS tools wrapped as @tool functions...]
)
options = ClaudeAgentOptions(
    mcp_servers={"forgeos": mcp_server},
    hooks={"PreToolUse": [HookMatcher(matcher="*", hooks=[kernel_hook])]},
)
```

### Manifest example

```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: my-claude-agent
spec:
  stack: anthropic-agent-sdk
  llm:
    chat_model: claude-haiku-4-5-20251001
    provider: anthropic
  tools: [web_search, memory__read, memory__write]
```

### Deployed and tested

- **Cloud Run service:** `mode-c-claude-sdk-609114458603.europe-west1.run.app`
- **Proven:** YAML manifest change → kernel blocks tool remotely → zero code redeploy

---

## Anthropic Managed Agents Adapter (`anthropic-managed`)

**File:** `stacks/anthropic_managed/adapter.py` (~250 lines)

### What it does

Deploys agents to Anthropic's hosted runtime via the Managed Agents REST API. Anthropic runs the agent in a gVisor sandbox. ForgeOS gates at session creation and tracks usage after completion.

### How it works

```
ForgeOS deploy → kernel admit() → POST /v1/agents → POST /v1/environments
  → Store managed_agent_id + managed_env_id

ForgeOS invoke → POST /v1/sessions → POST /v1/sessions/{id}/events
  → Agent runs INSIDE Anthropic's sandbox (tools execute freely)
  → Poll GET /v1/sessions/{id} until status == "idle"
  → Read usage → record in process table
```

### Important limitation

**No per-tool interception.** Built-in tools (bash, read, write, web_search) run ungoverned inside Anthropic's sandbox. ForgeOS can only:
- Gate at session level (budget/ACL check before creating session)
- Track usage after completion
- Gate custom MCP tools (if exposed via ForgeOS MCP server URL)

### API endpoints used

```
POST /v1/agents                    → Create agent definition
POST /v1/environments              → Create container config
POST /v1/sessions                  → Start execution session
POST /v1/sessions/{id}/events      → Send user message
  Body: {"events": [{"type": "user.message", "content": [{"type": "text", "text": "..."}]}]}
GET  /v1/sessions/{id}             → Poll status + usage
GET  /v1/sessions/{id}/events      → Get response events
```

**Required header:** `anthropic-beta: managed-agents-2026-04-01`

### Proven live

- Created agent `agent_01GNp3YU4yN2ezL2KSa77q53` on Anthropic
- Created environment `env_015tUzAjSaBWy3kNXtfgDSNA`
- Sent message, received 270-token response from Claude Haiku
- Cost: $0.001163 per interaction

### Manifest example

```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: managed-customer-service
spec:
  stack: anthropic-managed
  llm:
    chat_model: claude-haiku-4-5-20251001
    provider: anthropic
  tools: [agent_toolset_20260401]
```

---

## OpenAI Agents SDK Adapter (`openai-agents`)

**File:** `stacks/openai_agents/adapter.py` (~250 lines)

### What it does

Dual-path adapter: when `openai-agents` SDK is installed, uses the real `Agent`/`Runner` with an `on_tool_start` hook for kernel governance. Falls back to the Responses API (`POST /v1/responses`) when the SDK is not available.

### How it works (SDK path)

```
Agent code → OpenAI Runner.run() → LLM decides to call tool
  → SDK fires AgentHooks.on_tool_start()
    → ForgeOSKernelHooks.on_tool_start() → runtime.check_tool()
      → If DENY: raise PermissionError → agent handles error
      → If ALLOW: @function_tool executes → result back to Runner
```

### How it works (Responses API fallback)

```
ForgeOS invoke → POST /v1/responses
  → OpenAI runs LLM → returns function_call
  → ForgeOS checks kernel → ALLOW/DENY
  → If ALLOW: ForgeOS executes tool, submits result
  → Poll until completed
```

### Key advantage over Anthropic Managed

The Responses API returns `requires_action` for custom function calls — **YOUR code executes the tool**. This means ForgeOS gates every custom tool call, unlike Managed Agents where tools run in Anthropic's sandbox.

### Three execution paths

| Priority | Path | When |
|----------|------|------|
| 1 | OpenAI Agents SDK (`Runner.run()`) | `openai-agents` package installed |
| 2 | Responses API (`POST /v1/responses`) | SDK not installed, `OPENAI_API_KEY` set |
| 3 | ForgeOS agentic loop | Neither available |

### SDK detection

```python
try:
    from agents import Agent as OAIAgent, Runner as OAIRunner, function_tool, AgentHooks
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
```

### Kernel hook (same pattern as Anthropic)

```python
class ForgeOSKernelHooks(AgentHooks):
    async def on_tool_start(self, context, agent, tool) -> None:
        decision = await runtime.check_tool(tool.name, {})
        if decision.denied:
            raise PermissionError(f"ForgeOS denied: {decision.reason}")
```

### Built-in tool mapping

| ForgeOS tool name | OpenAI tool type |
|-------------------|-----------------|
| `web_search` | `{"type": "web_search_preview"}` |
| `code_interpreter` | `{"type": "code_interpreter"}` |
| `file_search` | `{"type": "file_search"}` |
| Anything else | `{"type": "function", "name": "...", ...}` |

### Manifest example

```yaml
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: research-agent-openai
spec:
  stack: openai-agents
  llm:
    chat_model: gpt-4o-mini
    provider: openai
  tools: [web_search, memory__write]
```

---

## Comparison: All 8 Adapters

### Governance Depth

| Adapter | Tool gate | Budget gate | Audit | HITL | Namespace isolation |
|---------|----------|-------------|-------|------|-------------------|
| ForgeOS native | Per-tool | Per-tool | Every decision | Yes | Yes |
| CrewAI | Per-tool | Per-tool | Every decision | Yes | Yes |
| Google ADK | Per-tool | Per-tool | Every decision | Yes | Yes |
| OpenClaw | Per-tool | Per-tool | Every decision | Yes | Yes |
| Sandbox | Per-tool (HTTP proxy) | Per-tool | Every decision | Yes | Yes |
| **Anthropic Agent SDK** | **Per-tool (PreToolUse)** | **Per-tool** | **Every decision** | **Yes** | **Yes** |
| **Anthropic Managed** | **Session-level only** | **Session-level** | **Session events** | **No** | **Session isolation** |
| **OpenAI Agents** | **Per-tool (on_tool_start)** | **Per-tool** | **Every decision** | **Yes** | **Yes** |

### How Each Framework's Tool System is Wrapped

| Framework | Standard tool interface | ForgeOS wraps it as |
|-----------|----------------------|-------------------|
| ForgeOS | `_execute_tool()` function | Direct check in the loop |
| CrewAI | `BaseTool._run()` method | `class ForgeOSTool(BaseTool)` with kernel check in `_run()` |
| Google ADK | `FunctionTool(callable)` | `FunctionTool(async wrapper)` with kernel check inside |
| Claude Agent SDK | `hooks.PreToolUse` | One `_forgeos_kernel_hook()` for all tools |
| OpenAI Agents SDK | `AgentHooks.on_tool_start()` | `ForgeOSKernelHooks` subclass |
| Anthropic Managed | No tool interface | Session-level gate only |
| OpenAI Responses | `requires_action` response | Kernel check before executing function |

### Lines of Governance Code per Platform

| Platform | Auto (zero agent code) | Explicit (agent adds runtime calls) |
|----------|----------------------|-----------------------------------|
| ForgeOS native | 10 lines (in agentic_loop.py) | + N lines per call |
| CrewAI | ~20 lines (per-tool subclass) | + N lines per call |
| Google ADK | ~30 lines (per-tool wrapper) | + N lines per call |
| **Anthropic Agent SDK** | **~15 lines (one hook)** | **+ N lines per call** |
| **OpenAI Agents SDK** | **~15 lines (one hook)** | **+ N lines per call** |
| **Anthropic Managed** | **~5 lines (session gate)** | **N/A (can't add runtime inside sandbox)** |

### Fallback Chain

Every adapter has a fallback path when the SDK is not installed:

```
SDK available?
  ├── YES → Use real SDK runtime (ADK Runner, CrewAI Crew, Claude query(), OpenAI Runner)
  └── NO  → Fall back to ForgeOS native agentic loop (run_agentic_loop())
            Same governance, same tools, different LLM call path
```

---

## Adding a New Adapter

To add support for a new agent framework:

1. Create `stacks/<name>/__init__.py` and `stacks/<name>/adapter.py`
2. Implement `AgentStackAdapter`:
   ```python
   class MyAdapter(AgentStackAdapter):
       stack_name = "my-framework"
       async def create_agent(self, agent_def) -> str: ...
       async def invoke(self, agent_id, prompt, ...) -> AgentResult: ...
       def scaffold_files(self, agent_def) -> dict[str, str]: ...
   ```
3. Add the stack name to `STACK_NAMES` in `stacks/base.py`
4. Add to `STACKS` literal in `src/forgeos_sdk/manifest.py`
5. Find the framework's tool extension point and add a kernel check:
   - If it has hooks (like `PreToolUse` or `on_tool_start`): one hook for all tools
   - If it has tool classes (like `BaseTool`): subclass with kernel check in execute method
   - If it has callable tools (like `FunctionTool`): wrapper function with kernel check
6. Write tests in `tests/test_<name>_adapter.py`
