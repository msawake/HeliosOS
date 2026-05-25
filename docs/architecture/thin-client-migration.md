# Mission Control → thin client; delete the backend

## Context

Today ForgeOS ships a heavyweight FastAPI service (~130 endpoints in
`src/dashboard/fastapi_app.py`, 3.2k LOC) that the Next.js dashboard, the
`forgeos` CLI, and remote kernel callers all hit over HTTP. Auth, multi-tenant
RLS, audit log, chat sessions, run history, and a `/ws/agents` socket are all
owned by this service. The CLI is purely an HTTP client (`src/forgeos_sdk/client.py`),
which means every operation requires booting a server, holding a Postgres
connection, and issuing a token.

We want the OpenLens-to-EKS model:

- **`forgeos` CLI is the only canonical client.** It speaks directly to the
  Python platform (`src/platform/*`) in-process. No HTTP round-trip for local
  use.
- **Mission Control is a desktop shell** that wraps the existing Next.js UI
  and talks to a *local-only* loopback RPC exposed by the CLI. It executes
  commands, stores credentials in `~/.forgeos/`, and loads manifests from
  disk. Nothing about it is multi-user or remote.
- **No durable backend state.** Audit, run history, process table — all
  in-memory, single process lifetime. Durability is the agent's problem
  (it can write to its own files / external systems).
- **Delete the FastAPI service, multi-tenant/RLS plumbing, Cloud Run /
  k8s web manifests, server-side auth.** They're all dead weight in the
  thin-client model.

Decisions captured from the clarifying round:

| Concern | Choice |
| --- | --- |
| Delete scope | FastAPI backend **+** Next.js dashboard recast as a desktop shell |
| Credential store | Plaintext `~/.forgeos/credentials` (0600), kubectl-style |
| Persistence | Pure in-memory; no SQLite, no Postgres requirement |
| Worktree scope | Full migration in one go on the new worktree |

Open choices I am defaulting in this plan (call out if you want different):

- **Shell framework**: **Tauri** — smaller binaries (~10 MB), native keychain
  bridges available if we ever want them, and the security model matches a
  local-only client. Electron is the fallback if Rust toolchain ergonomics
  bite.
- **Local IPC**: **127.0.0.1 loopback HTTP** with a random port + one-time
  bearer printed to the user on `forgeos ui` start. Reuses ~90% of
  `dashboard/src/lib/api.ts` (only `apiBase()` changes). Keeps SSE/WebSocket
  semantics working for chat streams and live agent status without
  reinventing IPC.
- **Multi-tenant**: **delete `tenant_id` + RLS migrations**. In-memory single
  user means the column is dead weight. Migrations consolidated into a
  drop-only "remove RLS / drop tenant tables" doc; we don't ship the new
  Postgres schema because Postgres isn't required anymore.

## Worktree

```bash
git worktree add -b feat/thin-client-mission-control \
  ../forgeos-thin-client main
cd ../forgeos-thin-client
```

Branched from `main` (which actually contains the canonical examples and
recent platform changes; `leadforge` is divergent — we hit that on the prior
worktree).

## Migration in five chunks

Each chunk is a self-contained commit on the worktree branch. Order matters:
later chunks depend on earlier ones compiling.

### 1. CLI becomes platform-native (no behavior change for users)

**Goal**: every `forgeos` subcommand can run in-process against
`src/platform/*` without touching HTTP. The HTTP client stays, gated behind
`--remote <url>`, for backwards compatibility during the transition.

Files to edit:

- `src/forgeos_sdk/cli.py` — for each subcommand (`deploy`, `list`, `invoke`,
  `undeploy`, `health`, `validate`, `mc *`), branch on a new
  `ForgeOSContext.local_mode` flag. Local path imports `PlatformBootstrap`
  from `src/bootstrap.py` and calls registry/executor methods directly.
  Remote path keeps `ForgeOSClient` calls.
- `src/forgeos_sdk/client.py` — keep as-is; later it becomes an in-process
  adapter that exposes the same surface but skips HTTP.
- `src/forgeos_sdk/kernel.py` — already dual-mode; make in-process the
  default when no `FORGEOS_API_URL` is set (today HTTP is the default).
- Add `src/forgeos_sdk/local_runtime.py` — owns a process-singleton
  `PlatformBootstrap` instance, lazy-initialized on first CLI call. Tears
  down on process exit.

The platform classes already live as Python objects on `PlatformBootstrap`
(see `src/bootstrap.py:738-784` where the FastAPI app is wired up). We just
call them directly instead of through Uvicorn.

### 2. Local credential & config store

**Goal**: `forgeos config` writes to `~/.forgeos/`, kubectl-style.

Layout:

```
~/.forgeos/
  config.yaml          # current context, default profile, UI port pref
  credentials          # 0600; YAML: profiles + tokens / API keys
  manifests/           # symlinks or copies of user-curated agent manifests
```

Files to add/edit:

- New `src/forgeos_sdk/config_store.py` — read/write the two files, enforce
  `chmod 0600` on credentials, error loudly on world-readable.
- New CLI verbs in `cli.py`: `forgeos config set-credential`,
  `config use-profile`, `config view`. Mirror kubectl-config naming.
- LLM and MCP credential lookup (`src/core/database.py` `_load_env`, and
  per-MCP env var resolution in `src/mcp/server_manager.py`) gains a
  `config_store.get_credential(name)` fallback **after** env vars.

### 3. Delete the FastAPI backend

**Goal**: the HTTP server and everything that exists only to serve it goes
away.

Delete entirely:

- `src/dashboard/fastapi_app.py` (3.2k LOC).
- `infrastructure/docker/Dockerfile.mission-control`.
- `infrastructure/docker/cloudbuild.yaml` (Mission Control build target).
- `deploy/k8s/base/deployment-web.yaml`, the matching Service, Ingress, and
  any Kustomize overlay that targets the web pod.
- `src/dashboard/*` files that exist only to support the FastAPI app
  (e.g. SSE buffering helpers, auth middleware). Audit one file at a time —
  some helpers may be re-used by the local loopback in chunk 4.

Edit:

- `src/bootstrap.py` — drop `create_api_app()`, `run_api_server()`, the
  `--dashboard` flag, and the API-only env vars (`FORGEOS_API_URL`,
  `FORGEOS_API_TOKEN`, `FORGEOS_CORS_ORIGINS`). The CLI no longer boots a
  server; `PlatformBootstrap` becomes a pure library object.
- Remove auth-related code from anywhere outside `src/dashboard/` (search
  for `APIKeyHeader`, `dev_token`, `auth_enabled`).
- `pyproject.toml` — drop `uvicorn`, `fastapi`, server-only deps.
- `CLAUDE.md` — update the architecture section: "Platform layer is a
  library; CLI is the canonical client; Mission Control is a desktop shell."

### 4. Local loopback RPC for the desktop shell

**Goal**: `forgeos ui` starts a 127.0.0.1-only HTTP server that exposes the
~30 endpoints actually used by the dashboard (per the earlier exploration
of `dashboard/src/lib/api.ts`). Same JSON shapes, no auth middleware (bind
to loopback only), no tenant header. SSE streams and `/ws/agents` are
re-implemented as thin adapters over the in-process platform objects.

Files to add:

- `src/forgeos_sdk/local_server.py` — Starlette app (smaller than FastAPI,
  fewer deps; reuse Starlette since it's already a Uvicorn transitive).
  Exposes the same endpoint paths the dashboard expects so the React code
  needs minimal edits. Single-process, single-user; binds 127.0.0.1:0
  (random port), prints `forgeos://local/<port>?token=<hex>` to stdout.
- A `cli.py` subcommand `forgeos ui [--port N] [--no-open]` that boots the
  loopback server and launches the Tauri shell.

Files to edit:

- `dashboard/src/lib/api.ts` — `apiBase()` reads the port + token from a
  Tauri-injected env var (`__FORGEOS_LOCAL_URL__`) instead of
  `NEXT_PUBLIC_API_URL`. The `INTERNAL_API_URL` SSR branch goes away
  entirely (no SSR in a desktop shell).
- `dashboard/src/lib/auth.tsx` — drop login flow; auth is "you launched the
  app, you have the token". Token is still sent on every request so the
  loopback server can verify it (defense in depth against local malicious
  processes).
- `dashboard/next.config.js` — remove the `/api/*` rewrite (Tauri shell
  fetches absolute `__FORGEOS_LOCAL_URL__`).

### 5. Tauri desktop shell

**Goal**: `forgeos-mission-control` binary that bundles the Next.js export
+ a thin Rust shell that spawns `forgeos ui --no-open` as a child process,
reads the printed local URL, injects it into the WebView as
`window.__FORGEOS_LOCAL_URL__`, and kills the child on quit.

Files to add (all new):

- `mission-control/` — Tauri project root, parallel to `dashboard/`.
  - `mission-control/src-tauri/Cargo.toml` and `tauri.conf.json` (allowlist:
    file dialog only, no network beyond the spawned child).
  - `mission-control/src-tauri/src/main.rs` — spawns the CLI child, parses
    its stdout for the local URL line, sets env var for the WebView, sets
    up graceful shutdown.
  - `mission-control/package.json` — build pipeline: `next build && next
    export` into `mission-control/src-tauri/dist/`, then `tauri build`.
- `dashboard/` becomes static-exportable: switch `next.config.js` to
  `output: 'export'`, remove the rewrite, remove any server-only code paths.

Documentation:

- New `docs/architecture/thin-client.md` explaining the OpenLens analogy,
  the binary topology, and the security model (loopback only, token printed
  once).

## Test coverage to add

Each chunk lands with tests that prove the boundary held:

1. CLI in-process path — extend `tests/test_forgeos_sdk_cli.py` (or create
   it) to drive `forgeos deploy` end-to-end without booting a server. Assert
   that the agent appears in `PlatformBootstrap.registry` after the call.
2. Config store — `tests/test_config_store.py` checks file permissions, YAML
   round-tripping, refusal on world-readable credentials.
3. Backend deletion — `tests/test_no_http_dependency.py`: import everything
   in `src/forgeos_sdk/` and `src/platform/`, assert no `fastapi` /
   `uvicorn` symbol is reachable. Guards against regressions.
4. Loopback server — `tests/test_local_server.py` boots it on a random port
   and exercises the 5–10 most-used endpoints (agents list, deploy, invoke,
   approvals, chat/stream); confirms it binds to 127.0.0.1 only.
5. Tauri shell — Rust side: a unit test that parses the CLI's stdout
   "ready" line and extracts URL + token. End-to-end manual smoke
   documented in `docs/architecture/thin-client.md`.

## Files explicitly NOT changed in this plan

- `src/platform/*` (registry, executor, kernel, A2A, scheduler) — these are
  the library the CLI now imports. Their public surface must stay stable; we
  just stop fronting them with FastAPI.
- `examples/*` — agent manifests are unaffected; the deploy path now goes
  in-process instead of over HTTP, but manifests don't care.
- `stacks/*` — stack adapters keep their `AgentStackAdapter` interface.

## Verification end-to-end

After all five chunks land on the worktree:

```bash
# 1. CLI works locally with no server
forgeos deploy examples/jira-greeter-v2/manifest.yaml
forgeos list
forgeos invoke <id> "Run a manual pass."

# 2. No HTTP server is running
ss -lnt | grep -E ':5000|:8080'   # expect nothing

# 3. Desktop shell launches and works
cd mission-control && pnpm tauri dev
# expect: window opens, agent list populates, invoke works,
# approvals tab streams

# 4. Credentials persist between runs
forgeos config set-credential anthropic --value sk-...
ls -l ~/.forgeos/credentials          # -rw-------
forgeos invoke <id> "..."             # uses the stored key

# 5. Test suites green
PYTHONPATH=. python3 -m pytest tests/
ruff check src/ tests/
```

Negative checks:

- Grep `src/` for `from fastapi`, `uvicorn`, `tenant_id` — should return
  nothing outside the Tauri/loopback boundary.
- Build the Tauri binary and confirm it does not require Docker, Postgres,
  or a network connection to do its core flows.
