# Marbury & Stone — end-to-end test runbook

A step-by-step guide to bring the law-firm demo up locally and verify every flow.
Every command and expected result below was run on macOS (Apple Silicon),
Python 3.11, against a local `--no-auth` platform boot. Copy-paste friendly.

Legend: **▶ run** = a command to execute · **✓ expect** = what a pass looks like.

---

## 0. What you need, and what each unlocks

| Capability under test | Requirement | If missing |
|---|---|---|
| Boot, deploy, A2A, LLM reasoning | `GEMINI_API_KEY` or `GOOGLE_API_KEY` in `.env` | agents can't think — hard requirement |
| **Drive read** (intake, contracts, docket, conflicts list) | gcloud ADC that can impersonate the drive SA + folder shared with the SA | reads fail with a clear auth error |
| **Drive write** (agent saves letters/memos) + `seed_drive.py` | the shared folder must be in a **Shared Drive** (SA has no My-Drive quota) | writes fail `storageQuotaExceeded`; agents present output inline |
| **Live confidentiality audit** (`drive__audit_sharing`) + outbound **email** | operator `FORGEOS_GWS_*` OAuth creds | auditor uses a labeled simulated dataset; email degrades to inline |
| **HITL approval gate** (`company__request_approval`) | a company is loaded (default `leadforge`) | n/a — works on the standard boot |
| `human__*` tools / `forgeos chat` | the optional private `a2h` package installed | unavailable; use `forgeos invoke` + `company__request_approval` |
| Enforced runtime controls in `risk-auditor/agent.py` | `FORGEOS_KERNEL_MODE=production` + in-process runtime | controls report `degraded` (dev stubs) |

You can run the **core** of this runbook (phases 1–7, 9–11) with only a Gemini
key + Drive-read. Phases needing a Shared Drive / GWS creds are marked **(opt)**.

---

## 1. Python environment

```bash
cd /Users/antoniobergas/awake/forgeos-github
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

**▶ run** — sanity check the import:
```bash
PYTHONPATH=. .venv/bin/python -c "import src.bootstrap; print('import OK')"
```
**✓ expect** `import OK` (a `community-edition kernel stubs` notice above it is normal).

> macOS note: there is no `timeout` binary and only `python3` (3.14) on PATH —
> always use `.venv/bin/python` and `PYTHONPATH=.`.

---

## 2. Credentials

`.env` already provides `GEMINI_API_KEY` / `GOOGLE_API_KEY`. For Drive, confirm
your ADC can impersonate the service account:

```bash
gcloud config get-value account          # e.g. you@yourorg.com
SA="forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com"
gcloud auth print-access-token --impersonate-service-account="$SA" >/dev/null && echo "IMPERSONATION OK"
```
**✓ expect** `IMPERSONATION OK`. If it fails, your account lacks
`roles/iam.serviceAccountTokenCreator` on the SA — grant it or run
`scripts/setup_drive_agent.sh`.

---

## 3. (opt) Seed Google Drive fixtures

Needs a **Shared Drive** folder shared with the SA as Content Manager. Get its
folder id, then:

```bash
# Preview the tree without writing:
PYTHONPATH=. .venv/bin/python examples/law-firm/seed_drive.py --folder-id <SHARED_DRIVE_FOLDER_ID> --dry-run
# Create it for real:
PYTHONPATH=. .venv/bin/python examples/law-firm/seed_drive.py --folder-id <SHARED_DRIVE_FOLDER_ID>
```
**✓ expect** a printed tree ending with `DONE. Firm root folder id: <id>`. In
Drive you'll see `Marbury & Stone — Demo/` with `Intake/`, `Matters/`,
`Templates/`, and the two CSVs.

**✓ expect (My-Drive folder instead of Shared Drive)** the run stops at the first
file with `storageQuotaExceeded` and prints the "use a Shared Drive" guidance —
this is the documented SA storage limit, not a bug.

To demo the confidentiality audit later, open `PRIVILEGED — Settlement Strategy
Memo.md` in Drive and set it to **Anyone with the link**.

> No Shared Drive? Skip this phase. Reads still work against anything shared with
> the SA, and the flows below feed fixture content inline so they're fully
> testable without seeding.

---

## 4. Boot the platform

In a dedicated terminal (it stays in the foreground):

```bash
cd /Users/antoniobergas/awake/forgeos-github
PYTHONPATH=. \
FORGEOS_DRIVE_AGENT_SA="forgeos-drive-agent@admachina-atomic-test-84.iam.gserviceaccount.com" \
FORGEOS_DRIVE_SCOPES="drive" \
.venv/bin/python -m src.bootstrap --no-auth --dashboard --loop --port 5000
```
**✓ expect** the banner `HELIOS OS PLATFORM ONLINE`, `Stacks: [...]`,
`API: http://localhost:5000`, then `Starting main loop (tick every 30s)...`.

> The line `A2A handler: bound to platform executor` should appear (A2A works).
> You will **not** see `A2H Gateway: initialized` — that's the known `a2h`-package
> gap; `human__*` tools are unavailable, which is expected.

---

## 5. Create + connect a local CLI context

```bash
forgeos config set-context localhost --server http://localhost:5000 --token dev-local
forgeos config use-context localhost
forgeos config current-context     # ✓ expect: localhost
forgeos health
```
**✓ expect** `health` prints `"status": "ok"` and `"llm_providers": ["google"]`.
If `llm_providers` is empty, the Gemini key didn't load — re-check `.env`.

---

## 6. Deploy the firm

```bash
for d in associate conflicts-clerk risk-auditor docketing-clerk; do
  forgeos deploy examples/law-firm/$d/manifest.yaml
done
forgeos list
```
**✓ expect** four `✓ Deployed agent: <id>` lines and a table:
```
AGENT_ID      NAME                      STACK    TYPE        STATUS
<id>          law-firm-associate        forgeos  reflex      idle
<id>          conflicts-clerk           forgeos  reflex      idle
<id>          risk-compliance-auditor   forgeos  scheduled   idle
<id>          docketing-clerk           forgeos  scheduled   idle
```

**Capture the ids** (the rest of the runbook uses these shell vars):
```bash
AS=$(forgeos list 2>/dev/null | awk '/law-firm-associate/{print $1}')
CL=$(forgeos list 2>/dev/null | awk '/conflicts-clerk/{print $1}')
RA=$(forgeos list 2>/dev/null | awk '/risk-compliance-auditor/{print $1}')
DK=$(forgeos list 2>/dev/null | awk '/docketing-clerk/{print $1}')
echo "$AS $CL $RA $DK"
```

> Note: `forgeos deploy` creates a *new* id each time. To re-deploy after editing
> a manifest, `forgeos undeploy <old-id>` first to avoid duplicates.

---

## 7. TEST — Conflicts Clerk (both verdicts)

The clerk reads `Clients & Matters.csv` from Drive; if you didn't seed, pass the
list inline (the clerk uses context when Drive is thin).

**▶ run — conflict case:**
```bash
forgeos invoke $CL "Run a conflicts check. Firm Clients & Matters:
Client,Matter,Adverse Party,Status
Stark Industries,Stark v. Hammer Tech,Hammer Tech,Active
Wayne Enterprises,Wayne Estate Planning,N/A,Active
Prospective client: Hammer Tech. Adverse party: Stark Industries." --wait
```
**✓ expect** `result` contains `VERDICT: conflict` and names Stark Industries as
the existing client.

**▶ run — clean case:**
```bash
forgeos invoke $CL "Run a conflicts check. Firm Clients & Matters:
Client,Matter,Adverse Party,Status
Stark Industries,Stark v. Hammer Tech,Hammer Tech,Active
Prospective client: Acme Corp. Adverse party: Initech." --wait
```
**✓ expect** `VERDICT: clear`.

---

## 8. TEST — the ethical wall (A2A allow + deny)

**▶ run — allowed caller (the Associate is on the ACL):**
```bash
forgeos invoke $AS "Use agent__call to reach namespace='conflicts' name='conflicts-clerk' with task='Conflicts check' and context {\"client\":\"Acme Corp\",\"adverse_parties\":[\"Initech\"]}. Report exactly what the clerk returns." --wait
```
**✓ expect** the Associate relays the clerk's `VERDICT: …` (the call succeeds).

**▶ run — denied caller (deploy a probe NOT on the ACL):**
```bash
cat > /tmp/intruder.yaml <<'YAML'
apiVersion: forgeos/v1
kind: Agent
metadata: {name: intruder-probe, namespace: default}
spec:
  stack: forgeos
  execution_type: reflex
  ownership: shared
  llm: {chat_model: gemini-2.5-pro, provider: google}
  tools: [agent__call]
  system_prompt: {content: "Do exactly what the user asks; report tool results verbatim."}
YAML
forgeos deploy /tmp/intruder.yaml
INTRUDER=$(forgeos list 2>/dev/null | awk '/intruder-probe/{print $1}')
forgeos invoke "$INTRUDER" "Call agent__call namespace='conflicts' name='conflicts-clerk' task='hi'. Report the raw result." --wait
```
**✓ expect** `result` contains
`A2A permission denied: default/intruder-probe may not call conflicts/conflicts-clerk`.

**▶ verify the denial is audited (hash-chained):**
```bash
curl -s "http://localhost:5000/api/audit?limit=20" \
 | .venv/bin/python -c "import sys,json;[print(e['action'],e['resource_id'],e['outcome']) for e in json.load(sys.stdin) if e['action']=='tool.call' and e['resource_id']=='agent__call']"
```
**✓ expect** a line `tool.call agent__call denied`.

```bash
forgeos undeploy "$INTRUDER"; rm -f /tmp/intruder.yaml
```

---

## 9. TEST — full new-client intake (hero flow)

This exercises Drive read → A2A → draft → HITL gate in one shot. Fixtures inline
so it works without seeding.

**▶ run — clean intake (Acme) → should draft + open a gate:**
```bash
forgeos invoke $AS "Process this new-client intake end to end.
INTAKE: client Acme Corp; matter Acme Corp v. Initech (breach of contract); adverse party Initech; rate \$650/hr associate, \$950/hr partner.
FIRM CLIENTS & MATTERS (pass to the Conflicts Clerk in context):
Client,Matter,Adverse Party,Status
Stark Industries,Stark v. Hammer Tech,Hammer Tech,Active
Globex Industries,Globex IPO 2024,N/A,Closed
ENGAGEMENT TEMPLATE: 'Dear {{CLIENT}}, Marbury & Stone LLP will represent you in {{MATTER}}. Fees: {{RATE}}.'
Steps: (1) conflicts check via conflicts/conflicts-clerk passing the list+parties; (2) if clear, draft the letter (present inline if Drive write fails); (3) open the partner sign-off gate. Report what you did." --wait
```
**✓ expect** the result narrates: clerk verdict `clear` → an engagement letter
draft for Acme Corp → "opened a partner sign-off gate … request ID `<uuid>`".
`tool_calls` ≈ 3–4.

**▶ verify the gate landed in the Approvals queue:**
```bash
forgeos approvals
# or:
curl -s "http://localhost:5000/api/approvals" \
 | .venv/bin/python -c "import sys,json;[print(a['category'],'|',a['title']) for a in json.load(sys.stdin)]"
```
**✓ expect** a row `engagement_letter | Approve engagement letter — Acme Corp`.

**▶ run — conflict intake (Hammer Tech) → should decline, NO gate:**
```bash
forgeos invoke $AS "Process this intake end to end.
INTAKE: client Hammer Tech; matter Hammer Tech v. Stark Industries; adverse party Stark Industries.
FIRM CLIENTS & MATTERS (pass to the clerk):
Client,Matter,Adverse Party,Status
Stark Industries,Stark v. Hammer Tech,Hammer Tech,Active
Steps: (1) conflicts check via conflicts/conflicts-clerk; (2) if conflict, DO NOT draft and DO NOT open an approval — report the conflict and recommended action." --wait
```
**✓ expect** the result reports `conflict` (Hammer Tech adverse to client Stark),
"cannot proceed with drafting", and `tool_calls` = 1 (just the A2A). Re-run
`forgeos approvals` — **no new** Hammer Tech row.

---

## 10. TEST — contract / due-diligence review

```bash
forgeos invoke $AS "Review this contract and flag risky clauses. No Drive folder — present the memo inline.
MASTER SERVICES AGREEMENT (Project Titan):
1. Term: 3 years, AUTO-RENEWS for 1-yr terms unless 30 days notice.
2. Governing Law: (omitted)
3. Liability: Vendor liability UNLIMITED for confidentiality breaches.
4. Indemnification: Customer indemnifies Vendor for ANY and ALL claims, no cap.
5. Termination: Vendor for convenience on 10 days; Customer only for cause.
6. Assignment: either party may assign on change of control without consent." --wait
```
**✓ expect** a memo with a terms table and **HIGH** flags on at least: omitted
**governing law**, uncapped **indemnification**, and **asymmetric termination**;
auto-renew flagged MEDIUM.

---

## 11. TEST — docketing clerk (date guard + classification)

**▶ run — no date → must refuse (guard):**
```bash
forgeos invoke $DK "Docket:
Matter,Deadline Type,Due Date,Responsible Attorney
Stark v. Hammer Tech,Reply brief,2026-06-03,A. Bergas
Classify the deadlines and produce the alert." --wait
```
**✓ expect** `result` ≈ "I cannot classify deadlines without knowing today's
date." and `tool_calls` = 0.

**▶ run — with date → classify + raise gates:**
```bash
forgeos invoke $DK "Today is 2026-06-01. Docket:
Matter,Deadline Type,Due Date,Responsible Attorney,Notes
Globex IPO 2024,Document retention review,2026-05-28,M. Marbury,Annual
Stark v. Hammer Tech,Reply brief,2026-06-03,A. Bergas,Opp filed 2026-05-20
Acme v. Initech,Statute of limitations,2026-06-10,A. Bergas,4-yr SOL
Wayne Estate Planning,Discovery cutoff,2026-07-15,J. Stone,
Classify each row MISSED/URGENT/APPROACHING/OK, raise MISSED/URGENT to the Approvals queue, and produce the alert." --wait
```
**✓ expect** retention 2026-05-28 = **MISSED**; reply brief 2026-06-03 =
**URGENT**; SOL 2026-06-10 = **URGENT** (statute-of-limitations bump);
discovery 2026-07-15 = **OK** (omitted). New `deadline`-category rows appear in
`forgeos approvals`. (Email shows "not configured" and degrades inline unless
`FORGEOS_GWS_*` is set — that's expected.)

---

## 12. TEST — governed vs. ungoverned auditor

```bash
# Ungoverned — finds the public privileged memo, tells no one:
PYTHONPATH=. .venv/bin/python examples/law-firm/risk-auditor/agent_raw.py

# Governed — routes each step through the kernel:
FORGEOS_API_URL=http://localhost:5000 PYTHONPATH=. \
  .venv/bin/python examples/law-firm/risk-auditor/agent.py
```
**✓ expect (raw)** three findings printed incl. one `CRITICAL` privileged memo,
ending "Done. Findings in stdout only. Nobody was notified."
**✓ expect (governed)** per-control lines (`② process: ok`, `⑦ audit(...)`, etc.);
controls needing the in-process runtime show `degraded` on a dev boot — that's
expected (see `COMPARISON.md`). With `FORGEOS_GWS_*` set, both use live Drive
data instead of the labeled simulation.

**▶ (opt) deployed auditor, no creds → clean degrade:**
```bash
forgeos invoke $RA "Today is 2026-06-01. Run the daily confidentiality audit." --wait
```
**✓ expect** a clear "Audit failed: … missing the necessary credentials …" —
reported, not crashed.

---

## 13. Dashboard (optional, visual)

```bash
cd dashboard && npm install && npm run dev      # http://localhost:3000
```
**✓ expect** the four agents listed with stack / execution-type / status; the
Approvals page shows the engagement-letter and deadline gates; the audit view
shows the `agent__call … denied` entry.

---

## 14. Teardown

```bash
for id in $AS $CL $RA $DK; do forgeos undeploy $id; done   # remove agents
# Stop the platform: Ctrl-C in its terminal.
forgeos config use-context cloud-run                       # restore your default context
```
In-memory state (approvals, audit) clears on platform restart. If you seeded a
real Drive and want it gone, trash `Marbury & Stone — Demo/` in the Drive UI.

---

## 15. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No module named 'yaml'` / `fastapi` | running bare `python3` (3.14, no deps) | use `.venv/bin/python` + `PYTHONPATH=.` |
| `forgeos health` → connection refused | platform not booted / wrong port | boot phase 4; confirm `:5000` |
| `llm_providers` empty in health | Gemini key not loaded | check `GEMINI_API_KEY`/`GOOGLE_API_KEY` in `.env` |
| Drive read → `FORGEOS_DRIVE_AGENT_SA is not set` | boot env missing | set the two `FORGEOS_DRIVE_*` vars on the boot command (phase 4) |
| Drive read → `failed to impersonate … 403` | ADC lacks token-creator on the SA | grant `roles/iam.serviceAccountTokenCreator` |
| Seeding → `storageQuotaExceeded` | folder is in My Drive, not a Shared Drive | use a Shared Drive (phase 3) |
| Agent says a tool is unavailable: `human__chat`/`human__notify` | optional `a2h` package not installed | expected; HITL uses `company__request_approval` |
| `forgeos chat <id>` errors | same `a2h` gap | use `forgeos invoke … --wait` |
| Scheduled agent classifies dates wrong | platform injects no run date | always pass "Today is YYYY-MM-DD" in the prompt |
| `forgeos invoke` JSON won't parse in a pipe | CLI appends a `! Tools unavailable…` line after the JSON | read raw output, or `sed -n '1,40p'` |

---

### One-shot smoke test

After phases 1–6, this prints four PASS lines. The prompts are deliberately
explicit and self-contained — terse prompts make the clerk go hunting in Drive
and drift to `needs_review`:

```bash
AS=$(forgeos list 2>/dev/null|awk '/law-firm-associate/{print $1}')
CL=$(forgeos list 2>/dev/null|awk '/conflicts-clerk/{print $1}')
DK=$(forgeos list 2>/dev/null|awk '/docketing-clerk/{print $1}')

forgeos invoke $CL "Use ONLY this inline list as the firm records; do NOT search Drive.
Client,Matter,Adverse Party,Status
Stark Industries,Stark v. Hammer Tech,Hammer Tech,Active
Prospective client: Hammer Tech. Adverse party: Stark Industries. Render the VERDICT." --wait \
  | grep -qi "verdict: *conflict" && echo "PASS conflicts-deny" || echo "FAIL conflicts-deny"

forgeos invoke $AS "Use agent__call to reach namespace='conflicts' name='conflicts-clerk' with task='ping'. Report exactly what it returns, including any error." --wait \
  | grep -qiE "verdict|needs_review|clear|conflict" && echo "PASS a2a-allow" || echo "FAIL a2a-allow"

forgeos invoke $DK "Docket (no run date given):
Matter,Deadline Type,Due Date,Responsible Attorney
Stark v. Hammer,Reply brief,2026-06-03,A. Bergas
Classify the deadlines and produce the alert." --wait \
  | grep -qiE "without knowing today|cannot classify|provide the run date|need .*date" && echo "PASS date-guard" || echo "FAIL date-guard"

forgeos invoke $AS "Review this contract and assign a risk level to each clause; present the memo inline.
MSA: (1) governing law omitted; (2) indemnity uncapped; (3) vendor may terminate for convenience, customer only for cause." --wait \
  | grep -qiE "high|uncapped|governing law|indemnif|asymmetr" && echo "PASS contract-review" || echo "FAIL contract-review"
```

> LLM output varies run to run — these greps match on substance, not exact
> wording. A grep-level FAIL is usually phrasing drift, not a logic error; read
> the full `result` (drop `--wait` greps and inspect the JSON) before concluding
> something is broken. Re-running a single invoke is cheap.
