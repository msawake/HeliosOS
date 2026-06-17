# Testing `drive-chat-agent` via the forgeos CLI

A short, copy-paste walkthrough for verifying the deployed agent end-to-end
from your terminal. The agent and platform are already live; this is just
how to drive them.

> Pre-baked state from the deployment session of 2026-05-28:
>
> - **Agent id:** `18fbe425-41a` (`forgeos list` to confirm)
> - **Cloud Run revision:** `forgeos-platform-api-00037-dvk`
> - **Service account:** `forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com`
> - **Scope:** `drive` (so files shared with the SA via the Drive UI are visible)
> - **Known shared folder:** `1W1fZkc5cE0Dhx6JdPdPmyoOjr4eRQw9h` ("Helios OS Drive Demo")

---

## TL;DR — 30 seconds

```bash
forgeos health                           # platform up?
forgeos list | head                      # find the agent id
forgeos chat 18fbe425-41a                # interactive REPL
```

In the chat:

```
You> List the files in the folder 1W1fZkc5cE0Dhx6JdPdPmyoOjr4eRQw9h.
Agent> (compact list of files)

You> Read the file "How to use forgeos guide".
Agent> (summary)

You> Read it again, then append an H2 section "Smoke test 2026-05-29" with body "ping".
Agent> done — appended a Smoke test section
```

Open the Doc in Drive — the new section is there. Done.

---

## 1 — Sanity check the CLI + platform

```bash
which forgeos                            # /opt/homebrew/bin/forgeos (or wherever)
forgeos --help                           # commands: list, deploy, invoke, chat, …
forgeos health                           # 200 with platform info
forgeos chat --help                      # confirms the chat subcommand is wired
```

If `forgeos chat --help` says "unrecognized subcommand", reinstall the CLI from
`/Users/antoniobergas/awake/forgeos-thin-client/forgeos-cli`:

```bash
cd /Users/antoniobergas/awake/forgeos-thin-client/forgeos-cli
cargo build --release && install -m755 target/release/forgeos "$(which forgeos)"
```

## 2 — Locate the agent

```bash
forgeos list 2>&1 | grep drive-chat-agent
```

You should see something like:

```
18fbe425-41a   drive-chat-agent   forgeos   reflex   shared   idle   gemini-2.5-pro
```

If it's missing, re-deploy:

```bash
# Inline the system prompt and PUT to /from-yaml (the manifest references a file path
# which the server can't resolve, so we pre-substitute).
python3 - <<'EOF' > /tmp/dca.yaml
import yaml, pathlib
m = yaml.safe_load(open("examples/drive-chat-agent/manifest.yaml"))
m["spec"]["system_prompt"] = pathlib.Path("examples/drive-chat-agent/system_prompt.md").read_text()
print(yaml.safe_dump(m, sort_keys=False))
EOF

BASE=$(grep server ~/.forgeos/config.yaml | head -1 | awk '{print $2}')
TOKEN=$(grep token ~/.forgeos/config.yaml | head -1 | awk '{print $2}')
curl -sS -X POST "$BASE/api/platform/agents/from-yaml" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: text/yaml" \
  --data-binary @/tmp/dca.yaml
```

## 3 — Chat with it (the canonical UX)

```bash
forgeos chat 18fbe425-41a
```

This opens an A2H chat session over the platform's `/api/a2h/v1/chats/*`
endpoints, then loops: each line you type is posted as a `human` message and
the agent is invoked synchronously with the running conversation history;
its reply is posted back as an `agent` message and printed.

### Things to try

**List files:**

```
You> List the files in folder 1W1fZkc5cE0Dhx6JdPdPmyoOjr4eRQw9h.
```

Expected: 1–2 tool calls (`drive__list_files`), then a tight reply naming the
files in `name · mime · last-modified` form.

**Read a file:**

```
You> Read "How to use forgeos guide" from that folder and summarize.
```

Expected: 1–2 tool calls (`drive__find_by_name` then `drive__read_file`),
then a short summary.

**Update an existing file** (the round-trip happy path for Path B):

```
You> Read "How to use forgeos guide", then update it: append a new H2 section
You> at the end titled "Smoke test <today>" with body "round trip works". Keep
You> the rest as-is.
```

Expected reply: a short confirmation like *"done — appended a Smoke test
section"*. Open the Doc in Drive to visually confirm.

**Quit:**

```
You> /exit
```

Closes the chat session (POST `/api/a2h/v1/chats/<id>/close`).

### When gemini hallucinates / stops calling tools

Multi-turn coherence is currently weak: each `invoke` is stateless on the
platform side, so if the conversation runs long the agent sometimes drops
back to a stock "what can I help you with?" instead of doing the task.

Workarounds, ordered by effort:

1. **End the chat and start a fresh one.** `/exit` → `forgeos chat <id>` again.
   First turn after a fresh open is the most reliable.
2. **Phrase the prompt as a direct tool instruction.** Instead of *"please
   list the files"*, say *"Call drive__list_files with folder_id=<id>"*. The
   model latches onto the tool name.
3. **One-shot via `forgeos invoke`** (no chat, no history):

   ```bash
   forgeos invoke 18fbe425-41a "List the files in folder 1W1fZkc5cE0Dhx6JdPdPmyoOjr4eRQw9h"
   ```

## 4 — Verify what the agent actually did (don't trust the agent's self-report)

Independent re-read of the file as the SA (impersonated from your local
gcloud credentials):

```bash
FORGEOS_DRIVE_AGENT_SA="forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com" \
FORGEOS_DRIVE_SCOPES="drive" \
GCP_PROJECT_ID="admachina-atomic-test-84" \
PYTHONPATH=. /opt/homebrew/bin/python3.11 -c '
from src.platform.drive_tool import read_file
r = read_file(file_id="1WE_z91kJCLsjBq-jVgcRVZEob1Fteq2h5PYtT5uc2Ek", max_bytes=20000)
print(r["content"][-500:])
'
```

This requires you have `roles/iam.serviceAccountTokenCreator` on the
drive-agent SA (the `setup_drive_agent.sh` grants it to the Cloud Run SA;
to impersonate locally also add yourself):

```bash
gcloud iam service-accounts add-iam-policy-binding \
  forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com \
  --project=admachina-atomic-test-84 \
  --member="user:$(gcloud config get-value account)" \
  --role="roles/iam.serviceAccountTokenCreator"
```

(IAM propagation can take ~60 s the first time.)

## 5 — Inspect the audit trail

Every tool call is recorded with the args the LLM passed and the outcome.

```bash
BASE=$(grep server ~/.forgeos/config.yaml | head -1 | awk '{print $2}')
TOKEN=$(grep token ~/.forgeos/config.yaml | head -1 | awk '{print $2}')
curl -sS "$BASE/api/platform/agent-logs?agent_id=18fbe425-41a&limit=30" \
  -H "Authorization: Bearer $TOKEN" | python3 -c '
import json, sys
for e in reversed(json.load(sys.stdin)["events"]):
    print(f"{e[\"ts\"][:19]}  {e[\"type\"]:14}  {e[\"description\"][:90]}")
    det = e.get("details") or {}
    if det.get("args"):  print(f"    args: {json.dumps(det[\"args\"])[:200]}")
    if det.get("error"): print(f"    err:  {det[\"error\"][:200]}")
'
```

You should see `run.started`, the `tool.call` rows with full args, and
`run.completed` rows with token count + cost.

## 6 — Caveats you'll hit

- **`storageQuotaExceeded` when creating a file.** Service accounts have no
  personal Drive storage quota. They can read/update files shared with them,
  but **cannot create** in regular My Drive folders. For creation you need a
  **Shared Drive** with the SA added as Content Manager:
  https://drive.google.com → left sidebar → Shared drives → +New → add the
  SA as Content Manager → tell the agent the Shared Drive id in chat.
- **Multi-turn coherence is weak** (see Section 3). Use fresh chats or
  one-shot invokes when it matters.
- **Scope.** The SA is currently on the `drive` scope (full Drive). The
  narrower `drive.file` scope only sees files the user opened via a Drive
  Picker; UI-shared files are invisible. If you want to dial it back, set
  `FORGEOS_DRIVE_SCOPES=drive.file` on Cloud Run — but then UI shares stop
  working; switch to Shared Drives for the same effect with stricter scope.

## 7 — Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `forgeos chat: unrecognized subcommand` | Old CLI binary | Rebuild + install (Section 1) |
| Chat opens but agent gives generic "what can I help with?" | Stateless-invoke multi-turn drift | New chat or one-shot prompt; use explicit tool name |
| `drive list/read/update` returns `403 The user does not have sufficient permissions` | SA isn't shared on the file | Right-click in Drive → Share → add SA email → Editor → make sure to click *Share anyway* on the "external" warning |
| `drive create failed (403): storageQuotaExceeded` | SA has no personal quota | Use a Shared Drive; SA as Content Manager |
| `drive 404 File not found` even though you shared it | Scope mismatch (drive.file vs drive) or share saved without "Share anyway" | Confirm `FORGEOS_DRIVE_SCOPES=drive` on Cloud Run + re-share with the warning dismissal |
| `forgeos chat` hangs after a prompt for a long time | LLM call in progress (often 20–40 s) or Cloud Run cold start | Wait; ctrl-C aborts the turn but the chat session stays open and resumable |

## 8 — Cleanup (optional)

```bash
# Stop the agent (but keep the SA + folder)
forgeos undeploy 18fbe425-41a

# Tear down the SA + impersonation grant (irreversible)
gcloud iam service-accounts delete \
  forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com \
  --project=admachina-atomic-test-84

# Drop the env var on Cloud Run
gcloud run services update forgeos-platform-api \
  --project=admachina-atomic-test-84 --region=europe-west1 \
  --remove-env-vars=FORGEOS_DRIVE_AGENT_SA,FORGEOS_DRIVE_SCOPES
```

The Drive folder you shared is yours — unshare or delete in the Drive UI as
needed.
