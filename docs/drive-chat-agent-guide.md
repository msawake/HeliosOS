# Launching the drive-chat-agent — build Google Docs and Sheets from chat

> Conversational agent that creates Google Docs and Sheets in a Drive folder
> you own, acting as a **dedicated service account** that only sees files you
> explicitly share with it. Authentication is **keyless** (impersonation from
> the platform's runtime SA). Conversation flows over the **A2H chat** method
> we just added.

End state: you run `forgeos chat <agent-id>`, ask for a "meeting notes Doc"
or "project tracker Sheet", and the agent creates it in your Drive folder —
real Google Docs / Sheets, formatted, openable, editable.

## TL;DR

```bash
# 1. one-time identity setup
PROJECT=admachina-atomic-test-84 ./scripts/setup_drive_agent.sh

# 2. share a Drive folder (in the Drive UI) with the SA email printed above
# 3. redeploy the platform (Cloud Build → Cloud Run; your usual path)

# 4. deploy the agent and chat
forgeos deploy examples/drive-chat-agent/manifest.yaml
forgeos list
forgeos chat <agent-id>
```

In the chat:
- `create a Google Doc called "Kickoff notes" with sections Goals, Decisions, Action items`
- `create a Google Sheet "Project Tracker" with columns Task, Owner, Status, Due`

Done — both appear in your shared folder as real Google Docs/Sheets.

## Prerequisites

- A GCP project (this guide uses `admachina-atomic-test-84`).
- The Helios OS platform deployed to that project as Cloud Run service
  `forgeos-platform-api`.
- `gcloud`, `forgeos` CLIs on your machine, signed in.
- **A Google Workspace edition that supports Shared Drives** (Business Standard
  and up; Enterprise plans always have it). You will create one Shared Drive
  for the agent to write into.

You do **not** need to be a Google Workspace admin, hold a service-account
key file, or have domain-wide delegation. The authorization is just "add the
SA as a member of a Shared Drive."

### ⚠ Important: why Shared Drive (not My Drive)

Service accounts **do not have personal Drive storage quota** — they can read
files you share with them, but cannot create files in My Drive folders. The
official Google guidance is: put the agent's working files in a **Shared
Drive**. Files in a Shared Drive use the Shared Drive's storage (not anyone's
personal quota), and the SA can create/modify them as a Content Manager.

If you only need the agent to *read and modify existing files*, a regular My
Drive folder shared with the SA works for read+update but NOT for create.

---

## Step 1 — One-time identity setup

```bash
PROJECT=admachina-atomic-test-84 ./scripts/setup_drive_agent.sh
```

This script is idempotent. It does five things:

1. **Enables APIs** — `iamcredentials.googleapis.com`, `drive.googleapis.com`.
2. **Creates the SA** `forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com`
   (if it doesn't exist).
3. **Discovers the Cloud Run runtime SA** for `forgeos-platform-api`.
4. **Grants** the runtime SA `roles/iam.serviceAccountTokenCreator` on the
   drive-agent SA — this is the keyless impersonation grant.
5. **Sets** `FORGEOS_DRIVE_AGENT_SA=<that email>` as an env var on the live
   Cloud Run service.

You should see, at the end:

```
✅ drive-agent identity ready.

Next steps (manual, ~30 s each):
  ...
        forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com
  ...
```

**Copy that email — it's the address you share Drive folders with.**

---

## Step 2 — Create a Shared Drive and add the SA as a member

In the Drive UI (https://drive.google.com):

1. Left sidebar → **Shared drives** → **+ New** (top-left).
2. Name it, e.g. **"Helios OS Demo"** → **Create**.
3. Open the new Shared Drive.
4. **Manage members** (people icon, top-right) → paste the SA email
   (`forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com`)
   → role **Content Manager** (or Manager) → uncheck "Notify people" →
   **Send**.
5. Open the Shared Drive again and copy its ID from the URL:
   `https://drive.google.com/drive/folders/<SHARED_DRIVE_ID>` — that's the
   value you'll give the agent in chat (Step 5).

> **Why this works.** Files in a Shared Drive don't consume any user's
> personal storage quota. The agent's SA (`drive.file` scope) can create
> and modify files in the Shared Drive when it's a Content Manager. Adding
> or removing the SA is the *only* control needed for revocation — no
> tokens to rotate, no keys to rotate.

**Alternative — read/update only** (no create). If you cannot use Shared
Drives, you can share an existing My Drive folder with the SA as Editor
instead. The agent will be able to *read* and *update* existing files in
that folder, but `drive__create_file` will fail with
`storageQuotaExceeded` because the SA has no personal quota.

---

## Step 3 — Redeploy the platform

The new code (`drive_tool.py`, `a2h_chat.py`, the FastAPI endpoints, the new
tool handlers) needs to be in the running image. Use your existing
Cloud Build → Artifact Registry → Cloud Run path:

```bash
# from the repo root
gcloud builds submit \
  --project=admachina-atomic-test-84 \
  --tag=europe-west1-docker.pkg.dev/admachina-atomic-test-84/forgeos/platform-api:drive-chat \
  --machine-type=e2-highcpu-8

gcloud run services update forgeos-platform-api \
  --project=admachina-atomic-test-84 --region=europe-west1 \
  --image=europe-west1-docker.pkg.dev/admachina-atomic-test-84/forgeos/platform-api:drive-chat \
  --min-instances=1 --no-cpu-throttling --memory=8Gi --cpu=4
```

(Your existing tag/CI flow works too — the point is just to get this branch's
code live.)

Verify health:

```bash
forgeos health
```

---

## Step 4 — Deploy the drive-chat-agent

```bash
forgeos deploy examples/drive-chat-agent/manifest.yaml
forgeos list | grep drive-chat-agent
```

Grab the agent id from the list output. Example:

```
903e0874-XXX  drive-chat-agent  forgeos  event_driven  shared  running  gemini-2.5-pro
```

---

## Step 5 — Chat: build a Google Doc

```bash
forgeos chat <agent-id>
```

You'll see:

```
✓ chat opened: chat_xxxxxxxxxxxx  (agent: operations/drive-chat-agent)
  type /exit (or /quit) to end. EOF / Ctrl-D also works.

You>
```

Tell the agent which folder once (use the folder ID from Step 2):

```
You> Going forward, work inside folder 1AbCdEfGhIjKlMnO… when I ask you to create files. Confirm.
Agent> Got it — I'll use that folder for any files we create unless you tell me otherwise.
```

Now ask it to build a Doc. The agent has been taught to send HTML for Docs
so headings, bullets and bold come out as native formatting:

```
You> Create a Google Doc called "Project Kickoff Notes" with sections
     "Goals", "Decisions", and "Action items". Pre-fill Goals with three
     bullets: ship MVP by end of quarter, hire one engineer, publish a
     case study.
Agent> Created. Web link: https://docs.google.com/document/d/1Xy…/edit
       It has the three sections with the three goal bullets pre-filled.
       Want me to add anything to Decisions or Action items?
```

Open the link — it's a real Google Doc with H1/H2 headings, bulleted lists,
fully editable.

### What just happened (under the hood)

The agent called `drive__create_file` with:
- `mime_type="application/vnd.google-apps.document"` (target = Google Doc)
- `content="<h1>Project Kickoff Notes</h1><h2>Goals</h2><ul><li>…</li>…</ul>…"` (HTML)
- `folder_id="1AbCdEfGhIjKlMnO…"`

The drive tool auto-maps the Content-Type of the upload to `text/html`
(because the target is `vnd.google-apps.document`), Drive converts the HTML
into a real Doc, and returns the file id + web view link.

Authentication flow per tool call:
- Cloud Run runtime SA → mints a short-lived (~15 min) impersonation token
  for `forgeos-drive-agent` (keyless, scoped to `drive.file`).
- The Drive API accepts that token, sees the SA is Editor on your folder,
  permits the create.

---

## Step 6 — Chat: build a Google Sheet

Same chat session. CSV body → real single-sheet spreadsheet:

```
You> Create a Google Sheet called "Project Tracker" with columns Task,
     Owner, Status, Due. Pre-fill with three rows:
       1) Wire onboarding flow, Alice, In progress, 2026-06-15
       2) Set up audit logging, Bob, To do, 2026-06-20
       3) Customer interviews, Carol, Done, 2026-05-30
Agent> Created. https://docs.google.com/spreadsheets/d/1Zy…/edit
       4 columns, 3 rows + header. You can sort/filter from the toolbar.
       Want me to add a "Notes" column or another row?
```

Open the link — it's a real Google Sheet.

### What just happened

`drive__create_file` with:
- `mime_type="application/vnd.google-apps.spreadsheet"`
- `content="Task,Owner,Status,Due\nWire onboarding flow,Alice,In progress,2026-06-15\n…"` (CSV)
- `folder_id="1AbCdEfGhIjKlMnO…"`

The tool auto-maps the body's Content-Type to `text/csv`; Drive imports it
as a single-tab spreadsheet.

---

## Step 7 — Verify and iterate

Editing existing Doc/Sheet content is also fine:

```
You> Add a "Risks" section at the end of the Kickoff Doc with the bullet
     "Tight quarterly deadline if the new hire is delayed."
Agent> Read the doc, appended a Risks section with that bullet, saved.
       https://docs.google.com/document/d/1Xy…/edit
```

The agent reads (`drive__read_file` exports the Doc as text), composes the
new HTML, and overwrites (`drive__update_file` with `text/html` source).

List what's in the folder:

```
You> list all files in our folder
Agent> Found 2 files:
       • Project Kickoff Notes · vnd.google-apps.document · 2 min ago
       • Project Tracker        · vnd.google-apps.spreadsheet · 30 s ago
```

Close the chat:

```
You> /exit
✓ chat closed.
```

The session and every message are persisted on the platform (A2H chat store);
`forgeos approvals` won't show it (that's for `human__ask`/`notify`), but
the audit trail records every tool call with the principal that invoked it.

---

## How the pieces fit

```
You ─(forgeos chat)─▶ A2H chat session ─▶ invoke drive-chat-agent
                                              │
                          ┌───── drive__* tools (LLM picks per turn) ─────┐
                          ▼                                                │
                   forgeos-drive-agent  ◀── impersonated keylessly ── Cloud Run runtime SA
                          │
                          ▼
                Google Drive (only files shared with the SA)
```

- **Identity** is the dedicated SA (Plane B in `docs/security/agent-mcp-security-proposal.md`).
- **Authorization** is the Drive share you made in Step 2 — the SA cannot
  see anything you didn't share with it, full stop.
- **Conversation** rides the new A2H chat method (`src/platform/a2h_chat.py`)
  — multi-turn sessions, persisted, with long-poll fetch.
- **Compute** is the central platform container (T1 in the proposal's
  topology). For the next iteration you could move per-agent runs to
  autoscale pods (T2) — but for one drive agent talking to one human, T1
  is fine.

---

## Troubleshooting

### "FORGEOS_DRIVE_AGENT_SA is not set" in a tool result
The Cloud Run service hasn't been reloaded with the env var. Re-run:
```bash
gcloud run services describe forgeos-platform-api \
  --project=admachina-atomic-test-84 --region=europe-west1 \
  --format='value(spec.template.spec.containers[0].env)' | grep -i drive
```
Should show `FORGEOS_DRIVE_AGENT_SA=…`. If missing, re-run
`setup_drive_agent.sh`.

### "failed to impersonate … Unable to acquire impersonated credentials"
The runtime SA doesn't have `roles/iam.serviceAccountTokenCreator` on the
drive-agent SA. Confirm:
```bash
gcloud iam service-accounts get-iam-policy \
  forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com \
  --project=admachina-atomic-test-84
```
You should see the runtime SA listed with `roles/iam.serviceAccountTokenCreator`.
Re-run `setup_drive_agent.sh` if not.

### "drive create failed (403): storageQuotaExceeded" / "Service Accounts do not have storage quota"
The agent tried to create a file in a regular My Drive folder. Service
accounts have no personal quota. Move the work into a **Shared Drive** and
add the SA as Content Manager (see Step 2). Files in a Shared Drive use the
Shared Drive's storage, not anyone's personal quota.

### "drive list failed (403): The user does not have sufficient permissions"
The folder isn't shared with the SA, or the wrong scope was minted.
- Confirm sharing in the Drive UI: the SA email should appear as Editor.
- Check the scope on the service: `FORGEOS_DRIVE_SCOPES` env, default
  `drive.file`. For only-shared-files semantics that's correct.

### Agent says "I can't see any files yet"
That's literal: nothing has been shared with the SA. Share something, then
in chat: `list files`.

### Doc looks like plain text (no headings)
The agent sent `text/plain` or omitted HTML tags. In chat, prompt:
"Recreate it as a proper Doc with HTML formatting — H1 for the title, H2
for each section, `<ul><li>` for bullets." Or open `system_prompt.md` and
tighten the rule.

### "agent error: …" inside the chat
The agent saw a tool error and surfaced it. The most common ones are scope
mismatches and missing folder shares. `forgeos logs <agent-id>` (after the
turn finishes) shows the full tool call + result with `args:` and
`stderr_tail:` rendered inline.

### Long-poll feels slow
The CLI uses synchronous `invoke` per turn. Gemini takes ~3–10 s for a
simple turn. Tool calls (e.g. create_file) add a second or two. If a turn
hits a network blip the whole REPL waits — Ctrl-C breaks; the chat
session persists and can be resumed by id later.

---

## Limits worth knowing

- **Single-sheet Sheets only.** CSV import = one tab. Multiple sheets/tabs,
  formulas, charts, or formatting need the **Sheets API**, which is out of
  scope for `drive__create_file`. Open an issue if you want that.
- **HTML conversion fidelity.** Google's HTML→Doc converter handles
  headings, lists, bold/italic, basic tables, links. CSS is mostly ignored.
  For pixel-perfect docs use the **Docs API** directly.
- **No delete.** Intentional. If you want to remove a file, do it in the
  Drive UI. (Easy to add later if you want it.)
- **Read max 200 KB by default.** `drive__read_file` truncates above
  `max_bytes` (raise on demand).
- **No file ranges / patches.** `drive__update_file` is a full-content
  overwrite. The agent reads first, mutates in memory, writes back. Fine
  for normal-sized docs; not great for million-row sheets.
- **Auditability.** Every tool call is in the platform audit log with the
  args (the `args:` line in `forgeos logs`). Credentials never appear in
  the log (they're not in the tool args — the broker mints them inside the
  tool).

---

## Cleanup (optional)

To remove the agent + identity:

```bash
forgeos undeploy <agent-id>

# remove the impersonation grant + SA itself
gcloud iam service-accounts delete \
  forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com \
  --project=admachina-atomic-test-84

# unshare the Drive folder (in the Drive UI) — drag the SA out of Sharing.
```

That leaves no residual state. The chat sessions are in-memory in the live
Cloud Run instance; restarting the service clears them. (For durable chat
history, back the `InMemoryChatStore` with Postgres — a later iteration.)
