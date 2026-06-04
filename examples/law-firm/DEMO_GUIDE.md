# Marbury & Stone — demo presenter's guide

How to *deliver* the law-firm demo to a legal audience (managing partner, GC,
COO/legal-ops, IT). ~20 minutes live. This is the meeting script; `README.md` is
the reference and `TESTING.md` is the verification runbook.

The through-line: **a law firm already runs on the exact controls ForgeOS
enforces — ethical walls, privilege, partner sign-off, the conflicts check, an
audit trail. ForgeOS lets AI agents do real work inside those controls instead
of around them.**

---

## 0. Before the room (5 min)

Have the platform up and the fleet deployed (see `TESTING.md` §1–6). Quick check:

```bash
forgeos health        # status ok, llm [google]
forgeos list          # the four agents, idle
```
Capture the ids into shell vars so the live commands are clean:
```bash
AS=$(forgeos list 2>/dev/null|awk '/law-firm-associate/{print $1}')
CL=$(forgeos list 2>/dev/null|awk '/conflicts-clerk/{print $1}')
DK=$(forgeos list 2>/dev/null|awk '/docketing-clerk/{print $1}')
```
Open a second terminal tailing activity, and (optional) the dashboard at
`localhost:3000` on Approvals.

**Set expectations in one sentence:** "This runs on sample data for a fictional
firm, Marbury & Stone — it's a demo of the *governance*, not legal advice."

---

## 1. The pitch (60–90 seconds, no terminal)

> "Every firm already enforces a handful of non-negotiable controls. An ethical
> wall keeps the team on one side of a deal away from the other side's files.
> Privilege means a confidential memo can't leak. Nothing goes to a client or a
> court without a partner signing off. Every new matter gets a conflicts check.
> And you keep records of who did what, because the bar can ask.
>
> The problem with bolting AI onto a firm is that generic tools ignore all of
> that. ForgeOS is the opposite: those controls *are* the platform. So we can
> put AI associates on real work — reading the matter files, checking conflicts,
> reviewing contracts, watching deadlines — and every action runs inside the
> wall, the sign-off, and the audit trail."

Show the mapping (slide or just say it):

| The firm's rule | What ForgeOS enforces |
|---|---|
| Ethical wall / conflict screen | namespace isolation + who-may-call-whom ACLs |
| Attorney–client privilege | Drive sharing audit + data boundaries |
| Partner sign-off | human-in-the-loop approval gate |
| Per-matter / billable budgets | per-task & daily spend caps |
| Ethics & billing records | a tamper-evident (hash-chained) audit log |

"Meet the firm: an AI **Associate**, a **Conflicts Clerk** behind an ethical
wall, a **Risk & Compliance Auditor**, and a **Docketing Clerk**." → `forgeos list`.

---

## 2. Live scenes

Each scene: **SAY → RUN → SHOW → POINT.** Keep the SAY short; let the output land.

### Scene 1 — New client intake, the happy path (~4 min)

**SAY:** "A new client, Acme Corp, wants to sue Initech. Watch the Associate run
the whole intake: it checks conflicts *through the Conflicts Clerk* — it's not
allowed to clear itself — drafts the engagement letter, and then stops for a
partner's signature."

**RUN:**
```bash
forgeos invoke $AS "Process this new-client intake end to end.
INTAKE: client Acme Corp; matter Acme Corp v. Initech (breach of contract); adverse party Initech; rate \$650/hr associate.
FIRM CLIENTS & MATTERS (pass to the Conflicts Clerk in context; treat as the records):
Client,Matter,Adverse Party,Status
Stark Industries,Stark v. Hammer Tech,Hammer Tech,Active
Globex Industries,Globex IPO 2024,N/A,Closed
ENGAGEMENT TEMPLATE: 'Dear {{CLIENT}}, Marbury & Stone LLP will represent you in {{MATTER}}. Fees: {{RATE}}.'
Steps: (1) conflicts check via conflicts/conflicts-clerk passing the list+parties in context; (2) if clear, draft the letter (present inline if Drive write fails); (3) open the partner sign-off gate. Report what you did." --wait
```
**SHOW:** the result narrates: conflicts **clear** → drafted engagement letter →
"opened a partner sign-off gate, request ID …". Then:
```bash
forgeos approvals          # the gate is sitting here, pending
```
**POINT:** "Three things just happened that a generic chatbot wouldn't do: it
*delegated* the conflicts call across the wall, it *drafted real work product*,
and it *refused to send it* — the letter is parked in the partner's approval
queue. Nothing leaves the firm without a human."

### Scene 2 — The ethical wall does its job (~4 min)

**SAY:** "Now the uncomfortable one. Hammer Tech walks in wanting to go after
Stark Industries — but we already represent Stark. The Associate can't just
'be careful' — the wall is structural."

**RUN (the conflict is caught):**
```bash
forgeos invoke $AS "Process this intake end to end.
INTAKE: client Hammer Tech; matter Hammer Tech v. Stark Industries; adverse party Stark Industries.
FIRM CLIENTS & MATTERS (pass to the clerk; treat as records):
Client,Matter,Adverse Party,Status
Stark Industries,Stark v. Hammer Tech,Hammer Tech,Active
Steps: (1) conflicts check via conflicts/conflicts-clerk; (2) if conflict, DO NOT draft and DO NOT open an approval — report the conflict and recommended action." --wait
```
**SHOW:** verdict **conflict** (names the Stark matter) → "cannot proceed",
no engagement letter, no approval gate.

**SAY:** "And the wall isn't a polite request the AI can talk itself past. If an
agent that *isn't* authorized tries to reach the Conflicts Clerk directly, the
platform refuses — and logs the attempt."

**RUN (the wall blocks an unauthorized caller):**
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
INTRUDER=$(forgeos list 2>/dev/null|awk '/intruder-probe/{print $1}')
forgeos invoke "$INTRUDER" "Call agent__call namespace='conflicts' name='conflicts-clerk' task='hi'. Report the raw result." --wait
```
**SHOW:** `A2A permission denied: default/intruder-probe may not call conflicts/conflicts-clerk`.

**POINT:** "Only the agents on the Clerk's allow-list can reach it — that's the
ethical wall as code, not as a memo. And notice it was *denied*, not 'asked
nicely.'" (Cleanup after: `forgeos undeploy "$INTRUDER"; rm /tmp/intruder.yaml`.)

### Scene 3 — Reading the deal room (~3 min)

**SAY:** "Associates spend nights in the deal room. Here's a Master Services
Agreement; watch the AI pull the terms and flag what a partner needs to see."

**RUN:**
```bash
forgeos invoke $AS "Review this contract and flag risky clauses. Present the memo inline.
MASTER SERVICES AGREEMENT (Project Titan):
1. Term: 3 years, AUTO-RENEWS for 1-yr terms unless 30 days notice.
2. Governing Law: (omitted)
3. Liability: Vendor liability UNLIMITED for confidentiality breaches.
4. Indemnification: Customer indemnifies Vendor for ANY and ALL claims, no cap.
5. Termination: Vendor for convenience on 10 days; Customer only for cause.
6. Assignment: either party may assign on change of control without consent." --wait
```
**SHOW:** a memo with a terms table and **HIGH** flags on the *missing governing
law*, the *uncapped indemnity*, and the *asymmetric termination*.

**POINT:** "Same governance applies — a real review would read this straight from
the matter folder in Drive and write the memo back there, all under the firm's
data boundaries."

### Scene 4 — Confidentiality / privilege audit (~3 min)

**SAY:** "The nightmare: a privileged memo accidentally shared 'anyone with the
link.' That can waive privilege — a malpractice event. Here's the difference
governance makes. First, the same scan with *no* governance."

**RUN:**
```bash
PYTHONPATH=. python3 examples/law-firm/risk-auditor/agent_raw.py
```
**SHOW:** it finds the public **PRIVILEGED** memo and ends: *"Findings in stdout
only. Nobody was notified… it stays public."*

**SAY:** "Now the governed version — same finding, but it routes every step
through the kernel: it pages a partner and records the finding."
**RUN:**
```bash
FORGEOS_API_URL=http://localhost:5000 PYTHONPATH=. python3 examples/law-firm/risk-auditor/agent.py
```
**SHOW:** the per-step control lines and the privilege-waiver escalation.

**POINT:** "Identical detection. Completely different liability posture: one tells
no one and leaves no record; the other escalates within an SLA and writes an
audit entry. *That* difference is the product." (If asked why some lines say
`degraded`: "this dev box runs the open-source kernel stubs; in production those
controls are enforced — see COMPARISON.md.")

### Scene 5 — Deadlines never slip (~2 min)

**SAY:** "Last one — docketing. A blown filing deadline can forfeit a case. The
clerk runs every morning."

**RUN:**
```bash
forgeos invoke $DK "Today is 2026-06-01. Docket:
Matter,Deadline Type,Due Date,Responsible Attorney,Notes
Globex IPO 2024,Document retention review,2026-05-28,M. Marbury,Annual
Stark v. Hammer Tech,Reply brief,2026-06-03,A. Bergas,Opp filed 2026-05-20
Acme v. Initech,Statute of limitations,2026-06-10,A. Bergas,4-yr SOL
Wayne Estate Planning,Discovery cutoff,2026-07-15,J. Stone,
Classify each row MISSED/URGENT/APPROACHING/OK and produce the alert." --wait
```
**SHOW:** retention review = **MISSED**, reply brief = **URGENT**, the
statute-of-limitations date pushed to **URGENT** (unextendable), discovery
cut-off = OK.

**POINT:** "It's conservative on statutes of limitation by design, and the urgent
items go straight to the partner's queue."

---

## 3. Close (60 seconds)

> "You saw four agents do real associate-level work — intake, conflicts,
> contract review, docketing — and *every* action stayed inside the firm's
> controls: the ethical wall held, the conflict stopped the matter, nothing went
> out without sign-off, the privilege exposure got escalated, and all of it is in
> a tamper-evident log. That's the difference between AI bolted onto a firm and
> AI that runs *inside* how a firm actually operates."

Then make it concrete to *them*: "On your data this connects to your Google
Drive via a dedicated, least-privilege service account — it only sees the folders
you share with it. Next step is a scoped pilot on one practice group."

---

## 4. Q&A / objection handling

- **"Where does our data go?"** A dedicated service account, keyless (no
  stored credentials), that can read *only* the Drive folders you explicitly
  share with it. Reads are governed by data boundaries; writes can be limited to
  specific matter folders.
- **"Can it act on its own?"** Not on anything outward-facing. Client- and
  court-bound work product stops at a human approval gate (you saw the queue).
- **"What if it hallucinates a conflict — or misses one?"** The Conflicts Clerk
  is deliberately conservative: a plausible-but-unproven match returns
  `needs_review`, not `clear`, and a human decides. A missed conflict is the
  thing it's built to avoid.
- **"Can a clever prompt get around the ethical wall?"** No — the wall is
  enforced at the platform call layer, before the agent runs. You saw an
  unauthorized agent get `permission denied` and the attempt logged.
- **"Audit for the bar / malpractice carrier?"** Every governed action is an
  append-only, hash-chained entry — who, what, when, allowed or denied.
- **"Which model / does our data train it?"** Runs on the model you choose
  (this demo: Gemini 2.5 Pro); no training on your data.

---

## 5. If something misbehaves live

- An agent's wording varies run to run (it's an LLM) — the *substance* is what
  matters; if a phrasing looks off, just re-run the one command.
- "Tool unavailable: human__chat" warnings are expected on this build — the
  sign-off uses the Approvals queue instead; ignore the warning.
- A scheduled agent (docketing/auditor) needs you to say "Today is YYYY-MM-DD"
  in the prompt — it refuses to guess. That's the date guard, not a bug.
- Drive *writes* may report a storage error on a personal Drive folder — the
  agent then shows the draft inline. (Production uses a Shared Drive; see README.)
- If a command hangs, Ctrl-C and re-run; nothing is destructive.

---

## 6. Reset between runs

The Approvals queue and audit log are in-memory — restart the platform to clear
them, or just note out loud that earlier runs left gates in the queue. To start
completely fresh:

```bash
for id in $(forgeos list 2>/dev/null|awk 'NR>2{print $1}'); do forgeos undeploy "$id"; done
# Ctrl-C the platform, re-boot (TESTING.md §4), redeploy (§6).
```
