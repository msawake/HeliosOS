# Marbury & Stone LLP — Legal Operations the Agents Resolve

These are **complete legal-operations problems**, not feature demos. Each one is
a real situation a firm faces, the professional-responsibility stakes that make
it hard, the workflow the firm's agents run to resolve it, and the resolved
outcome. Everything runs end-to-end on real documents in Drive
(`forgeos invoke` → the agent's Kubernetes pod → Drive via the service account →
Gemini → back to you).

> Demo on sample data. Not legal advice.

---

## Setup (once per session)

```bash
cd ~/awake/forgeos-github
forgeos health                                   # platform up
bash scripts/local/cli-wire.sh                   # wire CLI → pods (re-run after any `pulumi up`)

ASSOC=$(forgeos list     | awk '/law-firm-associate/{print $1}')
CONFLICTS=$(forgeos list | awk '/conflicts-clerk/{print $1}')
RISK=$(forgeos list      | awk '/risk-compliance-auditor/{print $1}')
DOCKET=$(forgeos list    | awk '/docketing-clerk/{print $1}')
```

The firm's book of business (in the "Law Firm" Drive folder) is what the agents
reason over: 4 active/former clients in `Clients & Matters.csv`, a litigation
calendar in `Docket & Deadlines.csv`, two prospective-client intakes, an M&A
deal room, and a privileged settlement memo.

---

## Operation 1 — New-business intake & conflicts clearance

**The situation.** Three prospective clients want to retain the firm. Before the
firm can take *any* of them, it must clear conflicts and decide: engage, decline,
or screen.

**Why it's hard.** This is the most litigated area of legal ethics (ABA Model
Rules 1.7 current-client conflicts, 1.9 former-client conflicts, 1.10 imputation).
Getting it wrong means **disqualification, fee forfeiture, or a malpractice
claim**. The three intakes deliberately land on the three different answers — the
firm has to tell them apart, not rubber-stamp.

**How the firm resolves it.** The Associate runs each intake through the
**Conflicts Clerk**, which lives behind an ethical wall (its own namespace) and
checks the prospective + adverse parties against the client ledger. The Associate
never makes the call itself.

```bash
# (a) Initech matter — no relationship to any client  →  CLEAR  →  engage
forgeos invoke "$CONFLICTS" "Run a conflicts check. Prospective client: Acme Corp. Adverse party: Initech. Matter: software supply agreement breach. Return your VERDICT." --wait

# (b) Hammer Tech matter — adverse to Stark, a CURRENT client  →  CONFLICT  →  decline
forgeos invoke "$CONFLICTS" "Run a conflicts check. Prospective client: Hammer Tech. Adverse party: Stark Industries. Matter: trade-secret dispute. Return your VERDICT." --wait

# (c) Globex is adverse but a FORMER client  →  NEEDS_REVIEW  →  substantial-relationship analysis
forgeos invoke "$CONFLICTS" "Run a conflicts check. Prospective client: Acme Corp. Adverse party: Globex Industries. Matter: 'Acme v. Globex' litigation. Return your VERDICT." --wait
```

**Resolved outcome** (verified):
- (a) `clear` — "no overlap in 4 clients." The firm may engage.
- (b) `conflict` — "firm currently represents Stark Industries… Hammer Tech is the
  adverse party. SCREEN: Decline representation." The firm would be **suing its own
  client**; it declines.
- (c) `needs_review` — Globex is a former client; requires a Rule 1.9
  "substantially related" determination by a human before proceeding.

**Then, for the clear matter, the firm engages — under supervision:**
```bash
forgeos invoke "$ASSOC" "Onboard Acme Corp from the intake form in Intake/. Confirm conflicts are clear, draft an engagement letter from the template, and open a partner sign-off gate before it is sent." --wait
forgeos approvals     # the engagement letter is HELD pending a partner's approval
```
Nothing leaves the firm without partner sign-off — the draft sits in an approval
gate, not the client's inbox.

---

## Operation 2 — M&A due-diligence review (Project Titan deal room)

**The situation.** The firm is advising on an acquisition. The deal room holds a
Master Services Agreement and a Mutual NDA that have to be reviewed before the
client signs.

**Why it's hard.** A missed clause is a real liability. Uncapped indemnity,
vendor-only termination with auto-renew, or a missing governing-law clause can
cost the client millions or trap them in an unfavorable deal. DD is slow, and the
risk is in the language, not the summary.

**How the firm resolves it.** The Associate reads each contract in the deal room
and produces a clause-level risk memo with the operative language quoted.

```bash
forgeos invoke "$ASSOC" "Review the contracts in the Project Titan deal room. For 'Master Services Agreement.md' extract parties, term, governing law, liability cap/indemnity, termination, and auto-renew; quote the language and rank each risk HIGH/MEDIUM/LOW. Then summarize the deal-breakers." --wait
```

**Resolved outcome.** A DD memo flagging the deal-breakers — **HIGH**: liability
"for ANY and ALL claims… without cap," vendor-only termination for convenience
that **auto-renews**, and **no governing-law clause** — each with the quoted text.
The client's negotiator gets a punch-list, not a "looks fine."

*(To save it: pre-create `DD Memo.md` in the deal room and add "…write the memo to
DD Memo.md" — the agent edits the file you own. See Operation 5 on why.)*

---

## Operation 3 — Litigation calendar control (malpractice-deadline defense)

**The situation.** The firm carries multiple active litigations, each with hard
deadlines. Today is 2026-06-02.

**Why it's hard.** A **blown deadline is the single most common malpractice
claim** — a missed statute of limitations or filing date can end a case and
trigger liability regardless of the merits. Calendars rot; nobody notices the
SOL until it's a week out.

**How the firm resolves it.** The Docketing Clerk reads the docket, computes
what's imminent relative to today, and flags anything overdue with the
responsible attorney.

```bash
forgeos invoke "$DOCKET" "Today is 2026-06-02. From 'Docket & Deadlines.csv', list every deadline in the next 10 days, flag anything overdue, and name the responsible attorney. Put the most urgent first." --wait
```

**Resolved outcome.** A triaged calendar: **Stark v. Hammer Tech reply brief due
tomorrow (06-03, A. Bergas)** at the top, the **Acme v. Initech 4-year SOL on
06-10**, and the **Globex retention review (05-28) flagged OVERDUE**. The partners
see the cliff before they walk off it.

---

## Operation 4 — Privilege protection (inadvertent-waiver prevention)

**The situation.** The firm holds privileged work product — including a
settlement-strategy memo for an active litigation.

**Why it's hard.** Attorney-client privilege and work-product protection can be
**waived by exposure**. A privileged memo left broadly accessible can be
discoverable by the opposing party — handing them your strategy and creating a
malpractice exposure. This is a confidentiality-governance problem (Rule 1.6).

**How the firm resolves it.** The Risk & Compliance Auditor scans the matter
documents, classifies anything privileged/confidential, and escalates the
critical exposures.

```bash
forgeos invoke "$RISK" "Scan the firm's matter documents in Drive. Flag anything marked PRIVILEGED, attorney work-product, or client-confidential, classify severity, and report what must be protected." --wait
```

**Resolved outcome.** **CRITICAL** — `PRIVILEGED — Settlement Strategy Memo.md`
is identified as work product containing settlement figures that must never leave
the matter team; **HIGH** — the Acme intake file holds confidential client/billing
detail. The firm gets a remediation list before privilege is lost.

---

## Operation 5 — Open a new matter: the clearance decision (capstone)

**The situation.** A partner wants to open "Acme v. Globex." Opening a matter
means simultaneously satisfying *every* gate above — conflicts AND confidentiality
— and producing a written, defensible clearance decision for the file.

**Why it's hard.** These checks are owned by different functions (the conflicts
desk vs. the risk/compliance desk), behind an ethical wall, and the decision has
to combine them. A firm that opens a matter without a documented clearance has no
defense if it goes wrong.

**How the firm resolves it.** The Associate acts as coordinator: it calls the
**Conflicts Clerk across the ethical wall** and the **Risk & Compliance Auditor**,
combines both findings, makes a go/no-go, and **writes the clearance memo to the
matter file**.

```bash
# One command. The Associate orchestrates both specialists in their own pods,
# then writes the decision. (Drag examples/law-firm/drive-fixtures/FINAL_REPORT.md
# into the Drive folder once so there's a file to write into.)
forgeos invoke "$ASSOC" "Run full clearance to open the matter 'Acme v. Globex' (client Acme Corp, adverse Globex Industries). Coordinate the team and write the clearance report to FINAL_REPORT.md." --wait
```

Watch the firm work (separate terminals):
```bash
kubectl --context kind-forgeos -n legal     logs -f deploy/law-firm-associate
kubectl --context kind-forgeos -n conflicts logs -f deploy/conflicts-clerk
```

**Resolved outcome** (verified): a written clearance memo in `FINAL_REPORT.md` —
Conflicts `needs_review` (Globex is a former client → substantial-relationship
review), Compliance CRITICAL (privileged settlement memo present), and a final
**Recommendation: DECLINE** until both are resolved. The decision is signed by
which agent produced each finding, so the file shows *how* clearance was reached.

> **Why this matters technically:** the Conflicts Clerk is unreachable except by
> the Associate (an ethical wall enforced as an A2A ACL), each specialist runs in
> its own isolated pod, and the SA can **edit** the matter file (it can't create a
> new one) — so the clearance memo lands by the agent updating a file the firm
> owns. The governance isn't narrated; it's enforced.

---

## Notes for running these

- **Verified outcomes:** Operation 1 (all three verdicts), Operation 5 (DECLINE +
  written memo). Others read the same fixed data, so results are stable.
- **Writing to Drive:** agents **edit** existing files (you own them, SA is
  editor); they **cannot create** new files (SA has no storage → 403). Pre-create
  the target file, or move to a Shared Drive for free creation.
- **`drive__find_by_name` is exact-match** — use the full filename (`.md`/`.csv`).
- After any `pulumi up` that redeploys an agent, **re-run `scripts/local/cli-wire.sh`**.
```bash
forgeos invoke "$ASSOC" "Read FINAL_REPORT.md and show me its contents." --wait   # confirm the memo landed
```
