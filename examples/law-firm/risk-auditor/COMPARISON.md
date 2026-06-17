# Governed vs. ungoverned — why a law firm needs the runtime

Both files run the **same confidentiality scan** (`tools.py`): find Drive
documents shared by public link or whole-domain, and flag any that look
privileged. The audit logic is byte-for-byte identical. The only difference is
the Helios OS runtime governance in `agent.py`.

| Concern | `agent_raw.py` (ungoverned) | `agent.py` (governed) |
|---|---|---|
| A **privileged memo found public** | printed to stdout; nobody told | `ask_human` **pages the managing partner** (HITL, `privilege.public_exposure` gate) |
| Proof the audit ran / what it saw | none | every governed tool call lands in the **platform's hash-chained audit log** (`forgeos logs` / `GET /api/audit`) — the firm's ethics/compliance record. The script also calls `runtime.audit(...)` per finding to show the control point. |
| Spend | unbounded; runs away on a big Drive | `reserve` before, `commit` real cost after; `budget` ceiling enforced |
| "Stop" command | ignored | `pending_signals` drains the run |
| Quarantined agent | runs anyway | `process` phase gate refuses to start |
| Crash mid-scan | redo from scratch | `checkpoint` resumes tomorrow's diff |

## The law-firm point

A law firm's exposure here isn't "a misconfigured file." It's that an
over-shared privileged document can **waive attorney-client privilege** and
become a malpractice claim — and that, when the bar asks, the firm can **prove**
it was monitoring. The ungoverned agent finds the problem and tells no one,
leaving no record. The governed agent pages a partner within an SLA and writes
an immutable audit trail. Same finding; completely different liability posture.

## Run them

```bash
# Ungoverned — findings to stdout, no page, no record:
PYTHONPATH=. python3 examples/law-firm/risk-auditor/agent_raw.py

# Governed — against the running platform's kernel (pages a human, audits):
FORGEOS_API_URL=http://localhost:5000 PYTHONPATH=. \
  python3 examples/law-firm/risk-auditor/agent.py
```

If the operator's `FORGEOS_GWS_*` Drive creds aren't present, both fall back to
a **clearly-labeled simulated** dataset (one public privileged memo, one
domain-shared engagement letter, one intentionally-public brochure) so the
contrast is visible offline. A real run uses live `drive__audit_sharing` data.

### A note on what's actually enforced where

`agent.py` issues each governance control as a real kernel call, and logs the
result of each one transparently (`ok` / `degraded` / `SKIPPED`). Run standalone
against a dev platform you'll see several controls report **`degraded`**: a
standalone script connected via `Kernel.remote(url)` gets only the bare kernel,
and the default dev boot runs the **community-edition kernel stubs** ("all checks
disabled"). The controls (budget reserve/commit, A2H paging, checkpoint) are
enforced when the agent runs inside the platform's in-process runtime with
`FORGEOS_KERNEL_MODE=production`.

What is **already real on any boot** is the platform's own governance of
*deployed* agents: every tool call is written to the **hash-chained audit log**
(`forgeos logs` / `GET /api/audit`) and A2A ACLs are enforced at call time (an
unauthorized `agent__call` is denied and that denial is itself audited). Those
are the production-grade controls a firm relies on; this script is the explainer
for the per-step control *pattern*.
