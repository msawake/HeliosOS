# drive-chat-agent

Interactive Drive assistant. You chat with it via `forgeos chat <id>`; it
reads/writes the Drive files you've shared with its service account.

## Why a service account?

Following the project's security proposal (`docs/security/agent-mcp-security-proposal.md`):

- **No per-user OAuth refresh token to leak.** The agent has no human's
  credentials — it has a dedicated SA identity.
- **Keyless.** The platform impersonates the SA via `roles/iam.serviceAccountTokenCreator`
  — no JSON keys exist anywhere.
- **Authorization = Drive sharing.** The SA only sees files explicitly shared
  with its email. That *is* the "user gives access to the agent" step. The SA
  cannot enumerate or touch anything else.
- **Scope = `drive.file`.** The narrowest Drive scope; even within shared
  files the SA cannot list things outside what was shared with it.

## One-time setup

```bash
PROJECT=admachina-atomic-test-84 ./scripts/setup_drive_agent.sh
```

That script enables APIs, creates the SA (`forgeos-drive-agent@<project>.iam.gserviceaccount.com`),
grants `roles/iam.serviceAccountTokenCreator` to the Cloud Run runtime SA on
it (keyless impersonation), and sets `FORGEOS_DRIVE_AGENT_SA` on the platform.

Then in the Drive UI, share a folder with the SA email (Editor).

## Deploy + chat

```bash
forgeos deploy examples/drive-chat-agent/manifest.yaml
forgeos list                       # find the id
forgeos chat <agent-id>            # opens an A2H chat session
```

Try:

- `list my files` — should show what's shared with the SA.
- `create a file tasks.md with "[ ] Buy milk"` — agent creates it (visible in the shared folder).
- `add "[ ] Call Alice" to tasks.md` — agent reads, modifies, writes.
- `what's in notes.md?` — agent reads + summarises.
- `/exit` — closes the chat session.

## Tools the agent has

- `drive__list_files` / `drive__find_by_name` — discover.
- `drive__read_file` — text/markdown/JSON/Docs (export).
- `drive__update_file` — overwrite existing.
- `drive__create_file` — new file (default `text/markdown`).
- `human__chat` / `human__chat_check` — for agent-initiated questions mid-task.
- `memory__read` / `memory__write` — small KV store scoped to this agent.

No delete tool — intentional. No shell, no email, no GitHub. If you want
those, deploy a different agent.

## Where the wire goes

```
You ─(forgeos chat)──▶ A2H chat session ──▶ invoke agent
                                                 │
                       ┌────────── drive__* tools ──┐
                       ▼                            │
              forgeos-drive-agent SA  ◀── impersonates ── Cloud Run runtime SA
                       │
                       ▼
          Google Drive (only files shared with the SA)
```

The chat session and the agent's drive calls are independent: the chat is the
*UX*, the SA is the *identity*. Both flow through the A2H chat extension
(`src/platform/a2h_chat.py`) and the SA-based Drive tool
(`src/platform/drive_tool.py`).
