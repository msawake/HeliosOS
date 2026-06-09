# ForgeOS MCP Server

`forgeos-mcp-server.py` exposes the ForgeOS agent fleet to any MCP-compatible
client (Claude Code, Cursor, …). It is a **self-contained single file** — its
only third-party dependencies are `mcp` and `httpx`, and it imports nothing from
the ForgeOS source tree, so it runs from any working directory without
`PYTHONPATH`. This is the file that the repo's `.mcp.json` launches.

The importable package `src/forgeos_mcp` mirrors this file (same tools,
resources, and prompts) for the `forgeos-mcp` console script / installed-wheel
use case. A parity test (`tests/test_forgeos_mcp.py`) keeps the two tool sets in
sync.

It talks to a running ForgeOS API (FastAPI backend) over HTTP and wraps each
platform operation as an MCP tool.

## Install

```bash
pip install mcp httpx        # or: pip install -e ".[mcp]"
```

The interpreter that launches the server must have these two packages.

## Run

```bash
python3 tools/forgeos-mcp-server.py                          # stdio (Claude Code, Cursor)
python3 tools/forgeos-mcp-server.py --transport sse          # SSE (web clients)
python3 tools/forgeos-mcp-server.py --transport streamable-http --port 8000

# Equivalent, via the installed package:
python3 -m src.forgeos_mcp
```

Point it at a ForgeOS API first, e.g.:

```bash
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
```

## Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `FORGEOS_URL` | `http://localhost:5000` | ForgeOS API base URL |
| `FORGEOS_API_KEY` | _(empty)_ | Sent as `X-API-Key` when set (auth-enabled deployments) |
| `FORGEOS_USER` | _(empty)_ | Acting user, sent as `X-Forgeos-User` for per-user identity / credentials. Individual tools accept an `acting_user` argument that overrides it per call. |

## Register in Claude Code

This repo ships a `.mcp.json` that registers the server as `forgeos`:

```json
{
  "mcpServers": {
    "forgeos": {
      "command": "python3",
      "args": ["tools/forgeos-mcp-server.py"],
      "env": { "FORGEOS_URL": "http://localhost:5000", "FORGEOS_API_KEY": "", "FORGEOS_USER": "" }
    }
  }
}
```

Or add it manually:

```bash
claude mcp add forgeos -- python3 tools/forgeos-mcp-server.py
```

## Tool catalogue (24 tools, 5 resources, 3 prompts)

**Human-Agent Chat** — `forgeos_list_agents`, `forgeos_agent_detail`,
`forgeos_chat`, `forgeos_chat_history`

**HITL & Governance** — `forgeos_pending_approvals`, `forgeos_approve`,
`forgeos_reject`, `forgeos_a2h_pending`, `forgeos_a2h_respond`,
`forgeos_audit_log`, `forgeos_agent_contract`

**Fleet Control** — `forgeos_health`, `forgeos_fleet_status`,
`forgeos_process_table`, `forgeos_budget_overview`, `forgeos_deploy`,
`forgeos_deploy_yaml`, `forgeos_undeploy`, `forgeos_stop`, `forgeos_signal`

**Agent-as-a-Tool** — `forgeos_invoke`, `forgeos_fire_event`,
`forgeos_effective_policy`, `forgeos_billing_usage`

**Resources** — `forgeos://fleet`, `forgeos://health`, `forgeos://budgets`,
`forgeos://audit`, `forgeos://approvals`

**Prompts** — `review_approvals`, `fleet_report`, `agent_diagnostics`

## Tests

```bash
PYTHONPATH=. python3 -m pytest tests/test_forgeos_mcp.py
```
