# Helios OS Lens — UI spec

This is the contract the **forgeos-lens-builder** agent works against. When
it's unclear about a choice it asks the human via A2H; otherwise it iterates
opencode + `pnpm build` until the view in question renders and a PR is open.

## North star

OpenLens-style desktop client for Helios OS:

- One window, dark theme by default.
- Sidebar (left) for navigation.
- Main pane (right) renders the selected view.
- Status bar (bottom) shows current `forgeos` context + connection state.
- No login flow; the app is single-user, local-first. It talks to the
  `forgeos` CLI (Rust) via a `Command` shell-out for now, with a one-shot
  JSON stdout contract per call (see *Data sources* below).

## Stack

- **Shell**: Tauri 2 (Rust). Two-window app no; just one main window.
- **Frontend**: Vite + React + TypeScript + Tailwind. State via Zustand
  (light) or Jotai — agent chooses, asking human if it has a strong
  preference. Use shadcn/ui for primitives.
- **Build**: `pnpm` (agent must use pnpm, not npm or bun, per project
  preference — confirmed by initial A2H question).

## Sidebar groups (in order)

1. **Cluster** — Health snapshot. Connection status to the platform. Active
   `forgeos` context name. Version of the connected platform.
2. **Workloads** — One link per:
   - `Agents` — Table of deployed agents (name, namespace, stack, status,
     last run). Click an agent to open its detail panel (right pane).
   - `Runs` — Recent invocations across all agents. Filter by agent, by
     status, by time window.
3. **Governance**
   - `Approvals` — Pending A2H approval requests. Approve / reject inline.
   - `Questions` — Pending A2H text/choice questions. Inline text input
     submits to `forgeos answer <id> --text "…"`.
4. **Logs** — Live tail of agent invocations. Filterable by agent_id.
5. **Contexts** — kubectl-style context list. Switch between local /
   Cloud Run / staging. Settings stored at `~/.forgeos/config.yaml`.

## Agent detail panel

When clicking on an agent in the table:

- Header: agent name, namespace, stack badge, status pill.
- Tabs: Overview · Tools · Recent runs · A2H · Logs · Raw manifest.
- Overview: schedule, LLM (model + provider), tools count, budget caps,
  A2A ACLs, owner.
- Tools: list of allowed tool names with descriptions.
- Recent runs: last 20 invocations with prompt preview + output preview +
  duration + token count. Click a row to expand.
- A2H: pending + recently resolved requests originated by this agent.
- Logs: streaming, filterable by severity.
- Raw manifest: the deploy_request JSON, monospace.

## Data sources (CLI shell-out contract)

| View | Command |
| --- | --- |
| Sidebar status | `forgeos health` → `{ok, agents, version}` |
| Agents list | `forgeos list --json` |
| Agent detail | `forgeos describe <id>` (new verb — see TODOs) |
| Invoke | `forgeos invoke <id> "<prompt>"` (streamed) |
| Approvals list | `forgeos approvals list --json` |
| Approve / reject | `forgeos approvals {approve\|reject} <id>` |
| Questions list | `forgeos a2h pending --kind text,choice --json` |
| Answer | `forgeos answer <id> --text "<text>"` |
| Contexts list | `forgeos config get-contexts` |

If any of these commands don't exist yet, the agent should open a PR that
adds them in parallel with the UI — never assume a missing endpoint.

## Visual

- Background `#0f172a` (slate-900).
- Sidebar `#1e293b` (slate-800). Active link underline in `#22d3ee`
  (cyan-400) and bold text.
- Cards radius 8px, border `#334155` (slate-700), padding 16.
- Monospace font (JetBrains Mono) inside raw-manifest and logs views;
  Inter elsewhere.
- Status colors: green for OK, amber for warning, red for failed, gray
  for unknown.

## TODOs that the agent should track as separate PRs

Each of these is its own opencode pass → PR:

1. Tauri shell + main window + sidebar layout (no real data, mocks
   everywhere).
2. CLI shell-out plumbing — a `useForgeos<Cmd, Out>()` hook that runs the
   command and parses stdout JSON. Pipe errors to a toast.
3. Agents list view.
4. Agent detail panel: Overview tab only.
5. Approvals + Questions tabs (Governance).
6. Logs streaming.
7. Contexts switcher.
8. Polish: keyboard shortcuts, search across agents, command palette.

Each PR's commit history should be `feat(<area>): …` so the changelog is
self-documenting.

## Out of scope (don't build these yet)

- Multi-user / SSO.
- Writing agent manifests in-UI (use the YAML editor pattern only later).
- Direct Kubernetes integration (forgeos handles that already).
- Mobile / responsive — desktop only.
