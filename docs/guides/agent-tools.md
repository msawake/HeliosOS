# Agent Tools & MCP

Agents gain capabilities through **tools**. When an agent runs, the LLM can request tool calls, and ForgeOS executes them and returns the results. This page explains the three categories of tools and how to configure them.

## Tool Categories

| Category | Prefix | Source | Example |
|----------|--------|--------|---------|
| **MCP Tools** | `mcp__` | External MCP servers (stdio) | `mcp__filesystem__read_file` |
| **Custom Tools** | `company__` | In-process Python handlers | `company__request_approval` |
| **Platform Tools** | varies | Registered via `register_platform_tools()` | CRM, HTTP, messaging tools |

All tool calls route through a single `ToolExecutor` (`src/mcp/tool_executor.py`), which enforces per-agent whitelists and provides a unified audit trail.

---

## MCP Tools

MCP (Model Context Protocol) tools come from external servers connected at boot time. Each server discovers its tools via `list_tools()` and registers them with prefixed names.

### Naming Convention

```
mcp__{server-name}__{tool-name}

Examples:
  mcp__filesystem__read_file
  mcp__google-workspace__send_gmail_message
  mcp__slack__post_message
  mcp__postgres__query
```

### Configuring MCP Servers

MCP servers are configured in the company config YAML (`src/companies/leadforge/config.yaml`):

```yaml
mcp_servers:
  tier1:
    - name: "filesystem"
      package: "@modelcontextprotocol/server-filesystem"
      required: false
      args:
        - "/Users/me/Desktop"
        - "/Users/me/Downloads"

    - name: "slack"
      package: "@anthropic/mcp-server-slack"
      required: false
      env_vars:
        SLACK_TOKEN: "xoxb-..."
```

**Fields:**
- `name` -- Server identifier (used in tool prefix: `mcp__{name}__...`)
- `package` -- npm (`@scope/pkg`) or Python (`uvx pkg`) package
- `required` -- If `true`, boot fails when connection fails
- `args` -- Additional command-line arguments
- `env_vars` -- Environment variables passed to the server process

### Connection Lifecycle

1. At boot, `MCPServerManager` reads the config YAML
2. For each server, spawns a subprocess (`npx -y @pkg` or `uvx pkg`)
3. Initializes the MCP session and calls `list_tools()`
4. Registers discovered tools in `ToolExecutor` with prefixed names
5. Connection has a 30s timeout (configurable via `FORGEOS_MCP_BOOT_TIMEOUT`)

If a server fails to connect and `required: false`, boot continues without it.

### Client-Scoped MCP (Per-Client Isolation)

The `ClientMCPManager` provides per-client MCP connections with credential isolation:

- Each client can have its own Jira, Google Analytics, Slack with separate credentials
- Connections are lazy (created on first use) and LRU-cached (max 50, 30min TTL)
- When an agent with `ownership: client` calls an MCP tool, the client's dedicated connection is used
- No cross-client credential leakage

Client MCP configs are stored in the `client_mcp_configs` database table or registered in-memory for development.

---

## Custom Company Tools

Built-in tools that execute in-process (no external server needed):

| Tool | Description |
|------|-------------|
| `company__publish_event` | Publish to the internal event bus |
| `company__query_events` | Query events by department/status |
| `company__resolve_event` | Resolve a pending event |
| `company__request_approval` | Submit a HITL approval request |
| `company__check_approval` | Check approval status |
| `company__get_pending_approvals` | List pending approvals |
| `company__search_knowledge` | Search the knowledge base |
| `company__get_knowledge` | Get a knowledge entry by ID |
| `company__add_decision` | Record a decision precedent |
| `company__record_metric` | Record a business metric |
| `company__get_metric` | Get current metric value |
| `company__get_dashboard` | Get all metrics |

These tools are always available when a `CompanySystem` is initialized. No configuration needed.

---

## Assigning Tools to Agents

### Exact Match

Specify the full tool name:

```json
{
  "tools": [
    "mcp__filesystem__read_file",
    "mcp__filesystem__list_directory",
    "company__search_knowledge"
  ]
}
```

### Wildcard Match

Use `*` suffix to match all tools from a source:

```json
{
  "tools": [
    "mcp__filesystem__*",
    "company__*"
  ]
}
```

`mcp__filesystem__*` matches all tools from the `filesystem` MCP server.

### No Tools

If `tools` is empty or omitted, the agent gets **all available tools**. To restrict an agent to no tools, set `tools: ["__none__"]` (a non-matching placeholder).

### What Happens with Missing Tools

At **deploy time**: If an agent references tools that don't exist (e.g., MCP server not connected), the deploy succeeds with a warning logged. The missing tools are recorded in `agent.metadata["_missing_tools_at_deploy"]`.

At **invoke time**: The `build_tool_definitions()` function filters the agent's tool list against available tools. Missing tools are silently omitted -- the LLM only sees tools that actually exist.

The **tool whitelist** in `ToolExecutor.execute()` also enforces the agent's tool list at execution time, including wildcard matching.

---

## Tool Execution Flow

When an agent calls a tool:

```
1. LLM returns a tool_use block: {"name": "mcp__filesystem__read_file", "input": {"path": "/tmp/test.txt"}}
2. Agentic loop calls _execute_tool(name, input, context)
3. _execute_tool() applies retry (2 attempts) and per-tool timeout (default 60s)
4. ToolExecutor.execute() checks whitelist, then routes:
   - company__* -> in-process handler (sync Python function)
   - mcp__*     -> MCP server session.call_tool() (async, with 120s timeout)
   - unknown    -> {"success": false, "error": "Unknown tool"}
5. Result is serialized to JSON and appended to messages as tool_result
6. LLM sees the result and decides next action
```

### Retry Behavior

Tool execution is retried up to `FORGEOS_TOOL_MAX_RETRIES` times (default: 2) on:
- `asyncio.TimeoutError` -- tool took too long
- Any raised exception

Explicit error dicts (`{"error": "..."}`) are NOT retried -- they represent deliberate tool failures.

Backoff: `0.5 * 2^attempt` seconds between retries.

### Per-Tool Timeout

The default timeout is `FORGEOS_TOOL_TIMEOUT` (60s). Individual tools can override this by including `timeout_seconds` in their tool definition metadata.

---

## Adding Custom Tools

To add a new in-process tool:

1. Add the handler to `ToolExecutor._register_custom_tools()`:

```python
"company__my_custom_tool": self._handle_my_custom_tool,
```

2. Implement the handler:

```python
def _handle_my_custom_tool(self, input: dict, ctx: dict | None) -> dict:
    result = do_something(input["param1"])
    return {"output": result}
```

3. Add the tool schema to `get_custom_tool_definitions()`:

```python
{
    "name": "company__my_custom_tool",
    "description": "Does something useful",
    "input_schema": {
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "The input parameter"}
        },
        "required": ["param1"]
    }
}
```

The tool is immediately available to all agents that include it in their `tools` list.
