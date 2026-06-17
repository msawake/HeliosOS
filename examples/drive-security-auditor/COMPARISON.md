# Drive Security Auditor: Raw vs Helios OS Governed

Two files, same audit, completely different security posture.

```
agent_raw.py  →  96 lines,  0 runtime checks, findings go to stdout
agent.py      → 912 lines, 28 runtime checks, 7 phases of governance
```

## Side-by-Side: The Core Audit

### WITHOUT Helios OS (`agent_raw.py`)

```python
async def run_audit():

    # ── List users ──
    org = drive_tools.list_org_users()
    users = org.get("users", [])

    # ── Search sensitive files ──
    sensitive = drive_tools.search_sensitive_files()

    # ── Check permissions ──
    for f in sensitive.get("items", []):
        risks = drive_tools.check_file_risks(f)
        for risk in risks:
            print(f"  {risk['severity']}: {risk['detail']}")

    # ── Shared files ──
    shared = drive_tools.list_shared_files()
    for f in shared.get("items", []):
        risks = drive_tools.check_file_risks(f)
        for risk in risks:
            print(f"  {risk['severity']}: {risk['detail']}")

    print("Done. If a public NDA was found, it stays public.")
```

**That's it.** Finds a publicly shared client contract, prints to stdout, exits. The contract stays public. Nobody is notified. No proof the audit ever ran.

---

### WITH Helios OS — 28 Controls in 7 Phases (`agent.py`)

```python
async def run_audit():

    # ══ PHASE 1: BOOT & RESUME ══
    ①  cp = await runtime.last_checkpoint()          # resume from crash
    ②  contract = await runtime.contract()            # self-validate rules
    ③  proc = await runtime.process()                 # lifecycle check
    ④  caps = await runtime.list_capabilities()       # reuse existing tokens

    # ══ PHASE 2: PRE-FLIGHT ══
    ⑤  signals = await runtime.pending_signals()      # should I stop?
    ⑥  budget = await runtime.budget()                # can I afford this?
    ⑦  await runtime.check_data("drive-audit")        # namespace boundary
    ⑧  ticket = await runtime.reserve(3.00)           # lock budget
    ⑨  cap = await runtime.request_capability(        # 1-hour read access
         "drive-readonly", ttl=3600)

    # ══ PHASE 3: USER ENUMERATION ══
    ⑩  await runtime.check_tool("list_org_users")     # gate admin API
    ⑪  await runtime.audit("org.users_listed", {...})  # record scope
    ⑫  for user in users:                              # per-user boundary
         await runtime.check_data(f"user/{email}")     # CEO/Legal blocked

    # ══ PHASE 4: FILE SCAN ══
    ⑬  # check_tool() per API call (inside wrapper)   # enforce read-only
    ⑭  # audit() per API call (inside wrapper)         # record every call
    ⑮  budget = await runtime.budget()                 # mid-scan check
    ⑯  await runtime.checkpoint({users_scanned: N})    # per-user recovery
    ⑰  await runtime.audit("user.scan_completed")      # per-user record
    ⑱  # check_tool("search_sensitive") via wrapper    # gate sensitive search

    # ══ PHASE 5: RISK & FINDINGS ══
    ⑲  await runtime.audit("risk.detected", {          # per-finding record
         severity, file, sharing})
    ⑳  await runtime.check_a2a("gcp-auditor")         # cross-agent correlation

    # ══ PHASE 6: ESCALATION ══
    ㉑  await runtime.ask_human(                        # page security admin
         "security", "security-admin",
         "🔴 Client NDA publicly shared",
         priority="critical")
    ㉒  await runtime.ask_human(                        # notify file owner
         "security", "file-owner",
         "Your file is shared with external user",
         priority="high")
    ㉓  await runtime.notify_human(                     # daily team summary
         "security", "security-team",
         "Drive Audit: 🔴 3 CRITICAL findings")

    # ══ PHASE 7: CLEANUP ══
    ㉔  await runtime.commit(ticket, actual_cost)       # finalize budget
    ㉕  await runtime.release(ticket)                   # release on abort
    ㉖  await runtime.revoke_capability(cap.id)         # revoke Drive access
    ㉗  await runtime.checkpoint({completed: True})      # final state
    ㉘  await runtime.audit("drive_audit.completed")     # completion record
```

---

## What Each Control Prevents

| # | Phase | Runtime Call | Without It | Real Consequence |
|---|-------|------------|------------|-----------------|
| ① | Boot | `last_checkpoint()` | Starts from scratch | Crash at user 45/100 → rescan all, $3 wasted |
| ② | Boot | `contract()` | Doesn't know its own rules | Agent doesn't know it's forbidden from writing |
| ③ | Boot | `process()` | Runs when quarantined | Admin quarantined it for a reason — still runs |
| ④ | Boot | `list_capabilities()` | Requests duplicate tokens | 5 stale access tokens accumulate |
| ⑤ | Pre | `pending_signals()` | Ignores shutdown | Agent runs during maintenance window |
| ⑥ | Pre | `budget()` | No spending limit | $50 audit on a $3/day budget |
| ⑦ | Pre | `check_data()` | Accesses any namespace | Rogue auditor reads HR namespace |
| ⑧ | Pre | `reserve()` | Cost unknown upfront | Starts audit with $0.50 remaining — fails halfway |
| ⑨ | Pre | `request_capability(ttl)` | Permanent access | Agent retains org Drive read access forever |
| ⑩ | Users | `check_tool()` | Any admin API | Agent could call user deletion APIs |
| ⑪ | Users | `audit()` | No scope record | "Which users did you audit?" — no answer |
| ⑫ | Users | `check_data(user)` | Impersonates anyone | **Agent reads CEO's Drive, Legal's NDA folder** |
| ⑬ | Scan | `check_tool()` | Any Drive API | Agent calls `share_drive_file` or `delete` |
| ⑭ | Scan | `audit()` | No tool log | "What files did you access?" — no record |
| ⑮ | Scan | `budget()` mid | Burns full budget | Scans 5000 files, notices budget at file 4999 |
| ⑯ | Scan | `checkpoint()` | No progress saved | Rate-limited at user 80 → restart from 1 |
| ⑰ | Scan | `audit(user)` | No per-user record | Compliance: "prove you scanned user X" — can't |
| ⑱ | Scan | `check_tool()` | Sensitive search ungated | Agent searches for "password" files without approval |
| ⑲ | Risk | `audit(finding)` | Findings buried in bulk | Individual CRITICAL not traceable to specific audit |
| ⑳ | Risk | `check_a2a()` | No cross-agent intel | Drive public file + GCP open firewall = compound risk missed |
| ㉑ | Esc | `ask_human(admin)` | **Nobody paged** | **Public NDA found May 18 — still public June 1** |
| ㉒ | Esc | `ask_human(owner)` | Owner unaware | Person who shared the file doesn't know it's exposed |
| ㉓ | Esc | `notify_human(team)` | Team unaware | "Do you audit Drive daily?" — "I think so?" |
| ㉔ | Clean | `commit()` | Budget not reconciled | Monthly report: "$0 spent on Drive auditing" |
| ㉕ | Clean | `release()` | Budget locked forever | $3 reserved, audit crashed, budget never freed |
| ㉖ | Clean | `revoke_capability()` | **Access persists** | 1-hour token should expire, but explicit revoke is defense-in-depth |
| ㉗ | Clean | `checkpoint()` | No completion record | Next boot: "did yesterday's audit finish?" — unknown |
| ㉘ | Clean | `audit(completed)` | No proof it finished | Compliance: "prove the May 18 audit completed" — can't |

## The Numbers

|  | Raw | Helios OS Governed |
|--|-----|-----------------|
| Lines of code | 96 | 912 |
| Runtime controls | 0 | **28** |
| Phases | 1 (scan + print) | 7 (boot → pre → users → scan → risk → escalate → cleanup) |
| Per-tool checks | 0 | 2 per call (check + audit) |
| User boundary | None | Per-user `check_data()` (CEO/Legal blocked) |
| Capability management | None | Request with TTL + explicit revoke |
| Budget tracking | None | Reserve → mid-scan check → commit/release |
| Human escalation | None | Admin paged + file owner notified + team summary |
| Cross-agent | None | Correlate with GCP auditor for compound risks |
| Crash recovery | Full restart | Resume from last user checkpoint |
| Audit trail | None | Per-tool + per-user + per-finding + completion |
| Cost to add | — | **$0** (same Drive API calls) |

## Run Both

```bash
# Raw (no governance — findings go to stdout):
PYTHONPATH=. python3 examples/drive-security-auditor/agent_raw.py

# Governed (28 controls across 7 phases):
PYTHONPATH=. ATLAS_GATEWAY_URL=... ATLAS_GATEWAY_KEY=... \
  python3 examples/drive-security-auditor/agent.py
```

## Runtime Controls Comparison Across All Agents

| Agent | Raw | Governed | Key Governance Feature |
|-------|-----|----------|----------------------|
| **SRE GCP Auditor** | 0 | **10** + 2/tool | HITL for CRITICAL + daily notification |
| **Content Ops Pipeline** | 0 | **12** per piece | Client namespace isolation + HITL for regulated |
| **Drive Security Auditor** | 0 | **28** across 7 phases | Capability TTL + per-user boundary + file owner notify |
