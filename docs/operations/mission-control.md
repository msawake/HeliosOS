# Mission Control

Mission Control is the operator console for the Helios OS platform. It runs as a
small FastAPI + React app in `mission-control/` and talks to the platform
through HTTP proxies — there is no shared in-process state, so you can point it
at a local platform or a remote Cloud Run deployment with a single env var.

## Quick boot (local Postgres + platform + UI)

From the repo root, in three terminals:

```bash
make pg              # one-time: start the Postgres container
make migrate         # apply pending SQL migrations (idempotent)
make mc-platform     # boot the platform on :5099 (auto-frees the port)

# in a second terminal
cd mission-control && make dev-local   # Vite dev server on :5173, proxies to :5099
```

The `mc-platform` target now pre-cleans any process listening on its port, so a
stale `src.bootstrap` from an earlier session won't trigger
`address already in use`. The same cleanup is applied to `make backend`. To
free an arbitrary port without booting anything:

```bash
make free-port PORT=5099
```

## Tabs

### Fleet

Lists every process the platform knows about. Each row shows stack badge, name,
short PID, phase, namespace, tokens, cost, tool-call count, last heartbeat, and
per-row actions.

**SCHEDULED vs RUNNING.** A scheduled agent (cron `execution_type: scheduled`)
is in `phase=running` permanently — the platform doesn't park it between ticks.
The Fleet endpoint compares the live invoking-PIDs set against the agent's
execution type and surfaces a `display_phase` of `scheduled` whenever a
scheduled agent is *not* mid-invocation, plus a `next_run_at` ISO timestamp.
The badge renders **SCHEDULED** (cyan) with a tooltip showing the next fire
time, and flips to **RUNNING** (green) only while an invocation is actually
in flight.

**Stop vs Delete.** The Actions column now has two buttons:

- **STOP** — transitions the process to `phase=stopped`, removes the
  scheduler job, unsubscribes event triggers, and drops the checkpoint. The
  agent record stays in the registry. Disabled when the agent is already
  stopped. Calls `POST /api/platform/agents/{id}/stop`.
- **DELETE** — runs the same stop sequence, then unregisters the agent and
  deletes its on-disk config directory. Calls
  `DELETE /api/platform/agents/{id}`.

Both prompt a styled confirmation modal (no native `confirm()`).

**Upload Agent.** The `↑ UPLOAD AGENT` toolbar button opens a dialog that
accepts a `manifest.yaml` either as a file or pasted text. It POSTs to
`/api/platform/agents/from-yaml`, which validates against the deploy schema
and registers the agent. No restart required.

### Agent detail (right panel)

Click any row to open. Sections:

- **Process State** — phase badge, PID, tokens, cost, tool calls, heartbeat.
- **Registered Tools** — every tool the agent has resolved at deploy time.
- **System Prompt** — first 600 chars.
- **Actions** — `▶ RUN NOW`, `STOP`, `DELETE`.
- **Recent Runs** — last 20 invocations, polled every 5 s while the panel is
  open. Each row shows time, status, trigger (`manual` / `schedule` / `event`
  / `a2a`), tool-call and token counts, and duration. Click a row to expand
  prompt + output + error in-place. Backed by the new `agent_runs` table and
  `/api/platform/agents/{id}/runs`.
- **Metadata** — first 800 chars of the manifest metadata.

### Run Now

The RUN NOW dialog is **fire-and-forget**:

- Prompt is **optional**. An empty prompt is fine — the platform's LLM router
  has a defensive fallback that injects a neutral `"Begin."` user turn so
  Gemini-family providers don't 400 on empty `contents`.
- Clicking RUN NOW posts to
  `POST /api/platform/agents/{id}/invoke?async_mode=true`, which schedules the
  task with `asyncio.create_task(...)` and returns
  `{accepted: true, queued_at}` immediately.
- The modal closes, a small green toast confirms `Queued: <agent>`, and the
  run streams into RECENT RUNS and Governance → AGENT LOGS as it progresses.

### Governance

Three panes:

1. **HITL Inbox** (top left) — pending human approvals. This now merges
   *both* upstream sources:
   - Legacy `hitl_approvals` table (manifest `governance.human_in_loop` rules)
   - A2H gateway pending requests (`human__ask` calls from agents)
   Items are tagged `approval` or `a2h`. Approval items render the
   Approve/Reject buttons inline; A2H items show a hint that the agent's own
   interaction channel will handle the response. Polled every 2 s. Backed by
   `/api/hitl/pending`.

2. **Kernel Decision Feed** (top right) — allow/deny audit stream from the
   syscall pipeline (when `FORGEOS_SYSCALL_PIPELINE=1`) or the legacy hook
   chain. Reads `/api/audit`.

3. **AGENT LOGS** (bottom, full width) — unified per-agent activity feed.
   Filter bar: `all | runs | tools | hitl`. Sourced by merging
   `agent_runs` rows (emitted as `run.started` / `run.completed` /
   `run.failed`) with `platform_audit_log` entries where `resource_type =
   'tool'` (emitted as `tool.call`). Polled every 2 s. Backed by
   `/api/platform/agent-logs`.

### MCP servers tab

Full CRUD for platform-scoped MCP server bindings, persisted to the
`client_mcp_configs` table under the synthetic `_platform` client. Changes
require a platform restart to take effect — the dialog tells you so.

## API surface added for Mission Control

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/platform/agents/{id}/invoke?async_mode=true` | Fire-and-forget invoke; returns `{accepted, queued_at}`. |
| `GET`  | `/api/platform/agents/{id}/runs?limit=N` | Per-agent invocation history. |
| `GET`  | `/api/platform/agent-logs?limit=N&agent_id=…` | Merged run + tool-call event stream. |
| `GET`  | `/api/hitl/pending` | Unified HITL inbox (approvals + A2H). |
| `POST` | `/api/platform/agents/from-yaml` | Deploy from a raw manifest body. |
| `GET`/`POST`/`PUT`/`DELETE` | `/api/platform/mcp/servers[/{name}]` | Platform-scoped MCP server CRUD. |

The Fleet response (`GET /api/platform/fleet`) gains three new fields per row:
`display_phase`, `execution_type`, `next_run_at`.

## Schema additions

- **`agent_runs`** (migration `011_agent_runs.sql`) — per-invocation row with
  pid, agent_id, trigger, started_at, ended_at, status, prompt, output, error,
  tool_calls, tokens_used, duration_ms. Written by
  `PlatformExecutor.invoke()` via `src/platform/agent_runs_store.py`. No-op
  when there's no Postgres pool, so in-memory dev mode keeps working.
- **`platform_audit_log`** (migration `010_platform_audit_log.sql`,
  introduced earlier) — receives one `action=tool.call` row per tool
  invocation, written from `src/mcp/tool_executor.py` after dispatch. This
  works under both the syscall pipeline and the legacy hook chain.

## A2H (human approval) wiring

At boot the platform seeds a default human participant
**`operations/approver`** (pid `operator-default`) in the A2H gateway. Example
agents like `jira-ticket-greeter-v2` ask for that name, so out of the box their
`human__ask` calls resolve to a live participant and the request lands in the
HITL inbox instead of being auto-cancelled.

`ToolExecutor.__init__` runs *before* the A2H gateway is constructed, so its
`_custom_handlers` map needs to be rebuilt after wiring or the `human__*`
handlers won't be registered. Bootstrap does this automatically — if you wire
a different gateway at runtime, set `tool_executor._a2h_gateway` and then call
`tool_executor._custom_handlers = tool_executor._register_custom_tools()`.

Register additional humans via `gateway.register_human(HumanAgent(...))` —
operators can be modeled with custom namespaces, roles, channels, and
availability states.

`resolve_human(namespace, name)` is intentionally lenient: it tries an exact
match, then a case-insensitive match, then falls back to **any human
registered in the requested namespace**. This handles the common failure mode
where an LLM paraphrases the human name (e.g. asks for
`operations/greet_jira_ticket` instead of `operations/approver`) — the request
still routes to your real operator instead of being silently cancelled. The
fallback is logged so you can spot drift in the agent's prompt and tighten it
if you want strict routing.

## Recovery notes

- The `PostgresProcessTable` keeps its in-memory cache in sync with the
  `agent_processes` table, so restarts re-populate the Fleet from disk. Rows
  are loaded via the dict-row connection pool — every list/load path uses
  `dict(row)` directly, no zip-against-column-names (a previous bug silently
  swallowed all loaded processes).
- Recovery skips agents whose registry `status` is `failed`. If a Fleet row is
  missing after restart, check `SELECT status FROM platform_agents` and reset
  to `idle` if you want it re-deployed.

## Day-to-day commands

```bash
make migrate                    # apply pending SQL migrations
make mc-platform                # start platform (auto-frees port first)
make stop-mc-platform           # kill platform + free :5099
make free-port PORT=N           # kill whatever's on port N
make pg / make stop-pg          # Postgres container lifecycle
make psql                       # interactive psql against the local DB
```
