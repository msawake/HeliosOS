# Marbury & Stone LLP — a law-firm demo for Helios OS

A small, coordinated "firm" of governed agents that work a law firm's real
Google Drive. It exists to show, on one believable workload, why a law firm is
an almost perfect fit for Helios OS: the things a firm cares about map directly
onto Helios OS primitives.

| Law-firm reality | Helios OS primitive it becomes |
|---|---|
| **Ethical walls / conflict screens** (one side of a deal can't see the other) | **Namespace isolation + A2A `canBeCalledBy` ACLs** |
| **Attorney–client privilege / confidentiality** | **Drive sharing audit + data boundaries** |
| **Partner sign-off** before anything goes to a client/court | **HITL approval gate** (`human__chat` / `company__request_approval`) |
| **Billable-hour / per-matter cost control** | **Per-task & daily USD budgets** (`guardrails`) |
| **Ethics & billing audit trail** (who saw what, when) | **Hash-chained audit log** |
| **Mountains of documents** (deal rooms, discovery, matter files) | **`drive__read_file` / `drive__create_file` service account** |

## The cast

| Agent (dir) | Role | Lifecycle | What it shows |
|---|---|---|---|
| **`associate/`** (hero) | Legal Associate | `reflex` (chat) | Real Drive read/write, A2A caller, HITL sign-off |
| `conflicts-clerk/` | Conflicts Clerk | `reflex` (A2A callee) | **Ethical wall** — only the Associate may call it |
| `risk-auditor/` | Risk & Compliance Auditor | `scheduled` | **Governed vs. ungoverned** (privilege-waiver audit) |
| `docketing-clerk/` | Docketing Clerk | `scheduled` | Deadline / statute-of-limitations escalation |

Two flows live in the hero Associate:

1. **New-client intake** → read the intake form → A2A the Conflicts Clerk across
   the ethical wall → if clear, draft the engagement letter into the matter
   folder → **pause for partner sign-off** before it "sends".
2. **Contract / due-diligence review** → read the deal-room contracts → extract
   parties / term / liability / termination / governing law → flag risky clauses
   → write a review memo back to Drive.

## Google Drive setup

The agents act as a dedicated, keyless service account
(`forgeos-drive-agent@…`, the same one proven by `examples/drive-chat-agent`).
It can only touch what you **share with its email** — that sharing *is* the
authorization. One-time platform env:

```bash
export FORGEOS_DRIVE_AGENT_SA="forgeos-drive-agent@<project>.iam.gserviceaccount.com"
export FORGEOS_DRIVE_SCOPES="drive"   # full scope so it can see UI-shared folders
# scripts/setup_drive_agent.sh creates the SA + grants the runtime SA token-creator
```

### Use a Shared Drive for the fixtures (important)

A service account has **no Drive storage of its own**, so it cannot *own* files
in a regular "My Drive" folder — creating one returns
`storageQuotaExceeded` (folders are fine; only files need quota). For the agents
to **write** (engagement letters, DD memos) and for the seeder to create
fixtures, the firm folder must live in a **Shared Drive** whose storage belongs
to the org:

1. Create a Shared Drive (e.g. "Marbury & Stone — Demo").
2. Add the SA's email as a **Content Manager**.
3. Pass that Shared Drive's folder id to the seeder.

If you only have a My Drive folder, the agents still **read** everything shared
with the SA (intake, conflicts list, contracts, docket) — only writes fall back
to presenting the draft inline.

### Seed the demo fixtures

```bash
PYTHONPATH=. python3 examples/law-firm/seed_drive.py --folder-id <SHARED_DRIVE_FOLDER_ID>
# --dry-run to preview the tree first
```

It creates:

```
Marbury & Stone — Demo/
  Clients & Matters.csv          # existing clients (for the conflict check)
  Docket & Deadlines.csv         # deadlines (for the docketing clerk)
  Intake/
    New Client — Acme Corp.md    # CLEAN intake → letter gets drafted
    New Client — Hammer Tech.md  # CONFLICT intake → adverse to client Stark Industries
  Templates/Engagement Letter.md
  Matters/
    Acme v. Globex (Litigation)/
      PRIVILEGED — Settlement Strategy Memo.md   # for the confidentiality audit
    Project Titan (M&A)/Deal Room/
      Master Services Agreement.md   # uncapped liability, auto-renew, no governing law
      Mutual NDA.md
```

To demo the confidentiality auditor, open the **PRIVILEGED** memo in Drive and
set it to "Anyone with the link" so `drive__audit_sharing` surfaces a CRITICAL
privilege-waiver finding.

## Run it

Boot the platform (dev), then point the CLI at it with a local context:

```bash
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --loop --port 5000
forgeos config set-context localhost --server http://localhost:5000 --token dev-local
forgeos config use-context localhost
forgeos health
```

Deploy the firm and drive the hero:

```bash
for d in associate conflicts-clerk risk-auditor docketing-clerk; do
  forgeos deploy examples/law-firm/$d/manifest.yaml
done
forgeos list

# Hero — driven via invoke (`forgeos chat` needs the optional `a2h` package):
forgeos invoke <associate-id> "Process the new Acme Corp intake." --wait
forgeos invoke <associate-id> "Review the contracts in the Project Titan deal room." --wait
forgeos approvals                 # see the engagement-letter sign-off gate
```

Demo beats to hit:

- **Intake + ethical wall.** "Process the new Acme Corp intake" → the Associate
  reads the form, calls the Conflicts Clerk (`clear`), drafts the engagement
  letter, and **opens a partner sign-off gate** (visible in `forgeos approvals`
  / the dashboard Approvals queue; nothing "sends" until it's approved). Then
  try "Process the Hammer Tech intake" → the Clerk returns `conflict` (Hammer
  Tech is adverse to existing client Stark Industries) and the Associate
  **declines to draft** (and opens no gate).
- **The wall holds.** Any agent not named in the Clerk's `canBeCalledBy` ACL is
  denied at call time — and that denial is written to the hash-chained audit log
  (`forgeos logs <clerk-id>` / `GET /api/audit`).
- **Contract review.** "Review the contracts in the Project Titan deal room" →
  the Associate flags the MSA's uncapped liability, auto-renew, and missing
  governing-law clause, and writes a DD memo.
- **Governance contrast.** Run the auditor both ways:
  ```bash
  PYTHONPATH=. python3 examples/law-firm/risk-auditor/agent_raw.py     # finds the public privileged memo, tells no one
  FORGEOS_API_URL=http://localhost:5000 PYTHONPATH=. \
    python3 examples/law-firm/risk-auditor/agent.py                    # routes every step through the kernel
  ```
  See `risk-auditor/COMPARISON.md`.
- **Docketing.** Invoke `docketing-clerk` with **today's date** in the prompt
  (the platform scheduler does not currently inject the run date, so a
  date-sensitive agent must be told "today"; the clerk refuses rather than guess
  if it isn't) → it flags the past-due retention review, the 3-day reply-brief
  deadline (URGENT), and the approaching statute-of-limitations date, and raises
  the MISSED/URGENT items to the Approvals queue.

## What's been verified locally

- Platform boots; CLI connects via the `localhost` context.
- All four manifests validate and deploy; `forgeos list` shows them.
- **Real** Gemini + **real** Drive read via the service account (the Associate
  lists Drive files and summarizes a live document).
- **A2A both ways:** the Associate's call to the Conflicts Clerk succeeds; a
  non-allowlisted caller is denied (`A2A permission denied …`), and the denial
  is recorded in the hash-chained audit log.
- **Full intake flow:** clean intake → `clear` verdict → letter drafted →
  `company__request_approval` gate opened (appears in `forgeos approvals`);
  conflict intake → `conflict` verdict → Associate declines, no gate.
- **Contract review:** flags omitted governing law, uncapped indemnity, and
  asymmetric termination as HIGH.
- **Docketing date guard:** refuses to classify without a run date; classifies
  correctly when given one.
- The governed/ungoverned auditor pair runs and prints the contrast.

### Environment caveats / known platform gaps (found while simulating)

- **`human__*` (A2H) tools are unavailable** unless the optional private `a2h`
  package is installed — `src/platform/a2h.py` imports it and the failure is
  swallowed at debug level, so the tools silently vanish (and `forgeos chat`
  won't work). These agents therefore use `company__request_approval` for HITL,
  which works on any boot.
- **The scheduler injects no run date** into the prompt/context, so
  date-sensitive scheduled agents must be told "today" (the clerks guard against
  guessing).
- **Service-account Drive writes/seeding need a Shared Drive** (SA storage
  limit); reads work against any folder shared with the SA.
- The **live** confidentiality audit and outbound email need the operator's
  `FORGEOS_GWS_*` Gmail/Drive creds; without them the auditor uses a clearly
  labeled simulated dataset and the agents present output inline.
- Dev boot runs **community-edition kernel stubs**; per-step runtime controls in
  `risk-auditor/agent.py` are enforced under `FORGEOS_KERNEL_MODE=production`.

> Demo on sample data — not legal advice.
