# Stack Adapters

ForgeOS supports four agent runtime frameworks. Each is wrapped in a **stack adapter** that implements the `AgentStackAdapter` interface from `stacks/base.py`. The platform layer calls the same methods regardless of which adapter an agent uses.

## Comparison

| | ForgeOS Native | CrewAI | Google ADK | OpenClaw |
|---|---|---|---|---|
| **File** | `stacks/forgeos/adapter.py` | `stacks/crewai/adapter.py` | `stacks/adk/adapter.py` | `stacks/openclaw/adapter.py` |
| **Runtime** | Platform agentic loop | CrewAI SDK (`Crew.kickoff()`) | ADK Runner (`run_async()`) | HTTP gateway subprocess |
| **SDK** | None (built-in) | `pip install crewai` | `pip install google-adk` | Node.js + openclaw2 directory |
| **LLM routing** | Via LLMRouter (Anthropic/OpenAI) | Via LiteLLM (inside CrewAI) | AnthropicLlm / LiteLlm / Gemini | Via gateway (OpenAI-compatible API) |
| **Tool bridging** | Direct `tool_executor.execute()` | `BaseTool` subclass per tool | `FunctionTool` wrapper per tool | Tools listed in AGENTS.md |
| **Multi-turn** | Full history via `run_agentic_loop(history=...)` | History injected into task description | History via ADK session service | History via platform fallback |
| **Fallback** | -- (this IS the fallback) | Platform agentic loop | Platform agentic loop | Platform agentic loop |
| **Best for** | Default choice, full flexibility | Role-based collaboration | Google ecosystem | Markdown-driven, file-first agents |

## The Common Interface

Every adapter implements these methods:

```python
class AgentStackAdapter(ABC):
    stack_name: str  # "forgeos", "crewai", "adk", "openclaw"

    async def create_agent(self, agent_def: AgentDefinition) -> str
    async def invoke(self, agent_id: str, prompt: str, context=None, history=None) -> AgentResult
    async def start_loop(self, agent_id: str) -> None
    async def stop(self, agent_id: str) -> None
    def get_status(self, agent_id: str) -> AgentStatus
    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]
    async def recover(self) -> int  # optional override
```

The platform executor calls these methods and never reaches into adapter internals.

---

## ForgeOS Native (`stacks/forgeos/adapter.py`)

The default and simplest adapter. Uses the platform's own agentic loop directly.

### How it works

**Create:** Stores the `AgentDefinition` in an internal dict. No external SDK initialization.

**Invoke:** Calls `run_agentic_loop()` from `src/platform/agentic_loop.py`:
1. Builds tool definitions from `tool_executor`
2. Constructs system prompt from agent definition
3. Runs the LLM -> tool_use -> execute -> tool_result -> LLM loop
4. Returns `AgentResult` with output text and tool call history

**Tools:** Passed directly to the LLM as Anthropic-format tool definitions. Tool calls route through `tool_executor.execute()` without any wrapping.

**When to use:** Default choice for all agents. No SDK dependency. Full access to all platform features. Best for reflex agents, scheduled agents, and any agent that primarily uses MCP tools.

---

## CrewAI (`stacks/crewai/adapter.py`)

Wraps the CrewAI framework for role-based agent collaboration.

### How it works

**Create:** If `crewai` SDK is installed:
1. Builds `agent_context` via `build_agent_context()`
2. Wraps each ForgeOS tool as a `CrewBaseTool` subclass (see "Tool Bridging" below)
3. Creates a `CrewAgent(role=name, goal=..., backstory=..., tools=[...])`
4. Stores in `_crew_agents` dict

If SDK is missing or creation fails: agent is stored but not in `_crew_agents`. Invoke falls back to platform loop.

**Invoke (real path):** If agent exists in `_crew_agents`:
1. Creates a `CrewTask(description=prompt, agent=crew_agent)`
2. Creates a `Crew(agents=[crew_agent], tasks=[task])`
3. Runs `crew.kickoff()` in a thread pool executor (blocking call)
4. Returns string output as `AgentResult`

**Invoke (fallback path):** If SDK missing or agent not in `_crew_agents`:
- Calls `run_agentic_loop()` (same as ForgeOS native)

### Tool bridging

CrewAI tools must be `BaseTool` subclasses with a `_run(**kwargs) -> str` method. The adapter creates a wrapper class for each ForgeOS tool:

```python
class ForgeOSTool(CrewBaseTool):
    name: str = "mcp__google-workspace__send_gmail_message"
    description: str = "Send email via Gmail API"

    def _run(self, **kwargs) -> str:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(
            tool_executor.execute(self.name, kwargs, agent_context)
        )
        loop.close()
        return str(result)
```

A new event loop is created per tool call because CrewAI runs in a worker thread (via `run_in_executor`), which has no active asyncio loop.

**When to use:** When agents need explicit role definitions and you want CrewAI's built-in delegation patterns. Good for multi-step workflows where the "crew" metaphor fits (e.g., a researcher + writer + editor pipeline).

---

## Google ADK (`stacks/adk/adapter.py`)

Wraps Google's Agent Development Kit for enterprise workflows.

### How it works

**Create:** If `google-adk` SDK is installed:
1. Builds model via `_build_adk_model()`:
   - `claude-*` -> `AnthropicLlm` or `LiteLlm("anthropic/...")`
   - `gpt-*` / `o3-*` -> `LiteLlm("openai/...")`
   - `gemini-*` -> bare string (ADK native)
2. Wraps ForgeOS tools as `FunctionTool` instances (async wrappers)
3. Creates `ADKAgent(name=..., model=model, instruction=..., tools=[...])`
4. Creates `Runner(agent=adk_agent, session_service=InMemorySessionService())`

**Invoke (real path):** If runner exists:
1. Creates a `Content` message from the prompt
2. Creates or reuses a session via the runner's session service
3. Consumes the event stream from `runner.run_async()`
4. Extracts text, tool calls, and token usage from events
5. Returns `AgentResult`

**Invoke (fallback path):** Falls back to `run_agentic_loop()` with history support.

### Tool bridging

ADK tools are `FunctionTool` instances wrapping async functions:

```python
async def _wrapper(**kwargs):
    result = await tool_executor.execute(tool_name, kwargs, agent_context)
    return result if isinstance(result, dict) else {"result": str(result)}

tool = FunctionTool(_wrapper)
```

ADK inspects the function signature to build a schema, so the wrapper accepts `**kwargs`.

**When to use:** When integrating with Google Cloud services, using Gemini models natively, or when you need ADK's built-in `SequentialAgent` / `ParallelAgent` composition patterns.

---

## OpenClaw (`stacks/openclaw/adapter.py`)

Wraps the OpenClaw framework, which uses a file-first approach with markdown-based agent configuration.

### How it works

**Create:**
1. Stores agent definition
2. Writes workspace files to `~/.openclaw-forgeos/workspaces/{agent-name}/`:
   - `SOUL.md` -- System prompt and personality
   - `AGENTS.md` -- Agent metadata and available tools
   - `HEARTBEAT.md` -- Schedule and event triggers
   - `memory.md` -- Persistent memory (initially empty)

**Invoke:**
1. Calls `_ensure_gateway()` to start the OpenClaw gateway subprocess (if not already running)
   - Gateway is a Node.js process serving an OpenAI-compatible REST API
   - Health-checked via `/health` endpoint
   - Protected by `asyncio.Lock` to prevent double-start
2. If gateway is running: sends HTTP POST to `/v1/chat/completions`
3. If gateway is unavailable: falls back to `run_agentic_loop()`

### Gateway lifecycle

The `OpenClawGateway` class manages the subprocess:
- `start()` -- Spawns `node openclaw.mjs gateway --port 18789`
- `_wait_for_ready()` -- Polls `/health` every 0.5s (30s timeout)
- `stop()` -- SIGTERM then SIGKILL after 5s
- `chat()` -- HTTP POST to `/v1/chat/completions` (300s timeout)
- `invoke_agent()` -- CLI invocation via subprocess (with kill-on-timeout cleanup)

The gateway auto-restarts after crashes (no one-shot gate).

**When to use:** When you want markdown-driven agent definitions (SOUL.md pattern), or when integrating with the OpenClaw ecosystem. Good for agents that emphasize memory and personality over tool use.

---

## Scaffold Files

Each adapter generates different files when an agent is deployed:

### ForgeOS
```
agent.py          -- AgentDefinition Python object
tools.py          -- Tool definitions list
prompts/system.md -- System prompt template
config.yaml       -- Agent configuration
```

### CrewAI
```
agents.py   -- CrewAgent definition
tasks.py    -- CrewTask templates
crew.py     -- Crew orchestrator
tools.py    -- Tool definitions
config.yaml -- Agent configuration
```

### ADK
```
agent.py              -- ADK Agent with model + tools
workflow.py           -- SequentialAgent wrapper
tools.py              -- FunctionTool wrappers
prompts/system_prompt.txt
config.yaml
__init__.py
```

### OpenClaw
```
SOUL.md              -- Agent personality and instructions
AGENTS.md            -- Agent metadata
HEARTBEAT.md         -- Schedule and triggers
SKILLS/default.yaml  -- Tool/skill definitions
MEMORY/long-term.md  -- Persistent memory
config.yaml
```

---

## Adding a New Stack Adapter

To add a new stack adapter (e.g., LangGraph):

1. Create `stacks/mystack/__init__.py` and `stacks/mystack/adapter.py`
2. Implement `AgentStackAdapter` with all 6 required methods
3. Set `stack_name = "mystack"`
4. Add `"mystack"` to `STACK_NAMES` tuple in `stacks/base.py`
5. Register in `src/bootstrap.py` `_register_adapters()`
6. Implement `scaffold_files()` for the new framework's file layout

The platform layer, dashboard, and API will automatically discover the new stack.
