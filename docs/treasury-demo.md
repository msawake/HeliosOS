# Treasury demo — 5 agents, A2A, per-agent Drive identity, dual human-in-the-loop

A conversational Treasury "team": one chat orchestrator that delegates to four
reconciliation specialists via agent-to-agent (A2A) calls. Each agent has its
**own Google service account**; the four specialists read their input data from
a **Google Drive folder you share with their SA** and write reports back. Two
human-in-the-loop paths are live: the **kernel** pauses every Drive write for
approval, and the agents **ask you to clarify** ambiguous requests.

## The cast

| Agent | Role | Service account | Drive |
|---|---|---|---|
| `kyriba-chat-orchestrator` | NL front door; routes via A2A | `drive-agent-kyriba@…` | identity only (no drive tools) |
| `bank-sap-reconciliation` | bank inflows ↔ SAP open AR | `drive-agent-bank-sap@…` | read + write |
| `debt-reconciliation` | scheduled debt service ↔ payments | `drive-agent-debt@…` | read + write |
| `po-reconciliation` | 3-way PO ↔ invoice ↔ payment | `drive-agent-po@…` | read + write |
| `mapping-classification` | counterparty/vendor → SAP id + GL | `drive-agent-mapping@…` | read only |

(`@…` = `@admachina-atomic-test-84.iam.gserviceaccount.com`.)

## What's already done (this environment)

- The **5 service accounts exist** in `admachina-atomic-test-84`; the local dev
  user and the runtime SA have `tokenCreator` on each (verified: impersonation
  mints a `drive.file` token; bank-sap lists its folder live).
- Manifests, runtime per-agent impersonation, write-gating governance, and the
  dashboard "Service account · Google Drive" card are wired and deployed.
- Verified e2e: per-agent SA auth, Drive-write → kernel `ask_human` (pauses),
  A2A routing (kyriba → specialist), real Qwen.

## What YOU must do once (the one external step) — share Drive folders

Service accounts have **no personal Drive quota**, so for *writing* reports the
folders must live in a **Shared Drive**. For each specialist:

1. In a **Shared Drive**, create a folder (e.g. "Treasury — bank-sap").
2. Add the agent's **SA email** as **Content Manager** (read-only agents:
   Viewer is enough — that's `mapping`).
3. Upload that agent's CSVs (they're in this repo at
   `src/companies/treasury/data/` — download and upload to Drive):

| Folder for | SA to share with | Upload these CSVs |
|---|---|---|
| bank-sap | `drive-agent-bank-sap@…` | `bank_inflows.csv`, `sap_open_items.csv`, `customer_mapping.csv` |
| debt | `drive-agent-debt@…` | `debt_schedule.csv`, `debt_payments.csv`, `debt_instruments.csv` |
| po | `drive-agent-po@…` | `purchase_orders.csv`, `vendor_invoices.csv`, `ap_payments.csv` |
| mapping | `drive-agent-mapping@…` | `customer_mapping.csv`, `vendor_mapping.csv`, `gl_account_mapping.csv` |

4. Copy each folder's **folder id** (from its Drive URL,
   `…/folders/<FOLDER_ID>`) and put it into the matching manifest's
   `spec.drive.folder_id` (replacing `REPLACE_WITH_FOLDER_ID`) in
   `src/companies/treasury/agents/<agent>.yaml`, then redeploy that manifest:
   `PUT /api/platform/agents/<id>/from-yaml` (or `forgeos deploy`).

> Tip: the SA email is shown (copy button) on each agent's dashboard **Overview
> → Service account · Google Drive** card.

## Replicate the platform locally

```bash
# 1. Boot Postgres + backend (:5000) + dashboard (:3000)
make start                       # or: make backend BACKEND_PORT=5000 && make dash
# Backend needs gcloud ADC for Drive impersonation (already present here):
#   gcloud auth application-default login

# 2. (fresh project only) provision the 5 per-agent SAs
for s in bank-sap debt po mapping kyriba; do ./scripts/provision_agent_sa.sh "$s"; done
#   or declaratively:  cd pulumi && pulumi up   (Identity component)

# 3. Deploy the 5 treasury agents
for a in kyriba-chat-orchestrator bank-sap-reconciliation debt-reconciliation \
         po-reconciliation mapping-classification; do
  forgeos deploy src/companies/treasury/agents/$a.yaml
done
```

Prereqs that are already satisfied here: the Qwen gateway key
(`secret:litellm-allycode-key`) and runtime-v2 (default on) for suspend/resume.

## The demo script (≈6 min)

Open the dashboard → chat with **kyriba-chat-orchestrator**.

1. **A2A + Drive read** — say: *"Reconcile today's bank inflows against SAP."*
   - The orchestrator routes to `bank-sap-reconciliation` (A2A `agent__call`).
   - The specialist reads its **Drive folder** (its CSVs), resolves counterparties
     via the `mapping-classification` agent (A2A), and reconciles.
   - It proposes writing the report → **the run pauses for approval** (kernel
     `ask_human` on `drive__create_file`). Show the inline **Approve** chip and
     the **Approvals** page. Approve → the report file appears in your Drive folder.
   - Talking point: *the agent only ever read what you shared with its SA, and it
     could not write to your Drive without your approval.*

2. **Agent-initiated clarification** — say something ambiguous:
   *"reconcile the stuff from yesterday-ish."*
   - The agent uses **`human__ask`** to ask which date/account before acting —
     the second HITL path (the agent clarifying, not the kernel gating).

3. **The other specialists** — *"any missed debt payments?"* → `debt-reconciliation`;
   *"check supplier over-billing"* → `po-reconciliation` (flags overbill / duplicate /
   maverick spend); *"who is counterparty ACME?"* → `mapping-classification`.

4. **Identity & governance** — open any specialist → **Overview**:
   - **Service account · Google Drive** card: its own SA email (the one you shared
     the folder with) + access level.
   - **Governance — tool approvals** card: reads `never`, writes `always` (gated).
   - **Logs / Approvals**: the audit trail, the gated `ask_human`, run rollups.

**Headline:** a real LLM (Qwen), agent-to-agent delegation, **per-agent cloud
identity**, data from **your own Drive** (least-privilege `drive.file` — the SA
sees only what you share), and **two human-in-the-loop controls** (kernel-gated
writes + agent-initiated clarification).

## Verified vs. pending

- ✅ Per-agent SA Drive auth (live), write→`ask_human` gating, A2A routing, real Qwen,
  dashboard cards, manifest round-trip, 57 unit tests.
- ⏳ Full reconciliation **with real data** requires the Drive folders above (the
  one step only you can do — sharing folders with the SAs). Once the folder ids are
  set, the end-to-end run completes and writes the report back.

## Known minor item

- A2A `agent__call` is not yet surfaced as a tool card in the chat transcript (the
  orchestrator's reply still shows the routed result). Functional, cosmetic only.
