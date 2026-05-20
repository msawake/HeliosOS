# Codebase Guardian: Raw vs ForgeOS Governed

```
agent_raw.py  →  67 lines,  0 runtime checks, findings go to stdout
agent.py      → 719 lines, 15 runtime checks, CRITICAL findings page security lead
```

## Side-by-Side

### WITHOUT ForgeOS (`agent_raw.py`)

```python
async def run_guardian():

    prs = gh_tools.list_open_prs(limit=10)

    for pr in prs.get("items", []):
        diff = gh_tools.get_pr_diff(pr["number"])
        findings = gh_tools.scan_diff_for_security(diff.get("diff", ""))

        for f in findings:
            print(f"  [{f['severity']}] PR #{pr['number']}: {f['description']}")

    print("Done. If SQL injection was found, it stays in the PR.")
```

---

### WITH ForgeOS (`agent.py` — 15 controls)

```python
async def run_guardian():

    # ── PRE-FLIGHT ──
    ①  state = await runtime.last_checkpoint()        # resume from crash
    ②  signals = await runtime.pending_signals()      # should I stop?
    ③  budget = await runtime.budget()                # $1 minimum to proceed
        ticket = await runtime.reserve(2.00)          # lock budget
    ④  proc = await runtime.process()                 # lifecycle check
    ⑤  await runtime.check_data(f"repo/{REPO}")       # repo access boundary

    prs = gh_tools.list_open_prs(limit=10)

    # ── PER-PR REVIEW ──
    for pr in new_prs:
        ⑥  await runtime.check_tool("get_pr_diff")    # can I read this PR?
        ⑦  await runtime.audit("pr.review_started")    # record start

        diff = gh_tools.get_pr_diff(pr["number"])

        ⑧  await runtime.check_tool("scan_diff")      # can I run security scan?
        findings = gh_tools.scan_diff_for_security(diff)
        ⑨  await runtime.audit("pr.security_scanned")  # record scan results

        review = await call_claude(diff)               # deep code review

        if critical_findings:
            ⑩  await runtime.ask_human(                # page security lead
                 "engineering", "security-lead",
                 "🔴 SQL injection in PR #42",
                 priority="critical")

        ⑪  await runtime.audit("pr.review_completed")  # record verdict

    # ── POST-ITERATION ──
    ⑫  await runtime.checkpoint({last_pr, reviews})    # crash recovery
    ⑬  await runtime.commit(ticket, actual_cost)       # finalize budget
    ⑭  await runtime.notify_human("dev-team", summary) # team notification
    ⑮  await runtime.audit("iteration.completed")      # iteration record

    await asyncio.sleep(300)  # 5 minutes, then repeat
```

---

## What Each Control Prevents

| # | Phase | Runtime Call | Without It | Real Consequence |
|---|-------|------------|------------|-----------------|
| ① | Pre | `last_checkpoint()` | Reviews same PRs after crash | Double cost, duplicate review comments |
| ② | Pre | `pending_signals()` | Ignores shutdown commands | Reviews during maintenance window |
| ③ | Pre | `budget()` + `reserve()` | No spending limit | Claude Opus reviews 50 PRs = $75 in one day |
| ④ | Pre | `process()` | Runs when quarantined | Admin suspended it for false positives — still runs |
| ⑤ | Pre | `check_data("repo")` | Accesses any repo | Reviews private repos it shouldn't (HR, legal) |
| ⑥ | PR | `check_tool("get_pr_diff")` | Reads any PR | Could read security-sensitive PRs (infra secrets) |
| ⑦ | PR | `audit("review_started")` | No start record | "Was PR #42 reviewed?" — no answer |
| ⑧ | PR | `check_tool("scan_diff")` | Security scan ungated | Could scan repos it shouldn't |
| ⑨ | PR | `audit("security_scanned")` | No scan record | Compliance: "prove you scan for secrets" — can't |
| ⑩ | PR | `ask_human("security-lead")` | **Nobody paged** | **SQL injection found, stays in PR, gets merged** |
| ⑪ | PR | `audit("review_completed")` | No verdict record | "What was the review outcome?" — no answer |
| ⑫ | Post | `checkpoint()` | Crash = full restart | Rate limited, restarts, re-reviews everything |
| ⑬ | Post | `commit()` | Budget untracked | "How much did code review cost this month?" — unknown |
| ⑭ | Post | `notify_human("dev-team")` | Team unaware | Developers don't know their PR was reviewed |
| ⑮ | Post | `audit("iteration.completed")` | No completion proof | Compliance: "prove guardian ran yesterday" — can't |

## The Killer Scenario

Guardian finds a `private_key` hardcoded in PR #87:

**With `agent_raw.py`**: Prints `[CRITICAL] PR #87: Private key in code` to stdout. The PR gets merged. The key is in production. Incident at 3 AM.

**With `agent.py`**: 
- ⑨ records the finding in the audit trail
- ⑩ pages the security lead: "🔴 CRITICAL: Private key in PR #87. Block, override, or investigate?"
- ⑪ records the verdict (BLOCK)
- ⑭ notifies the dev team
- The PR stays open until the key is removed

## The Numbers

|  | Raw | ForgeOS Governed |
|--|-----|-----------------|
| Lines of code | 67 | 719 |
| Runtime controls | 0 | **15** per iteration |
| Per-PR controls | 0 | 6 (check + audit + scan + audit + HITL + audit) |
| Per-day (288 iterations) | 0 | **~2,300** runtime calls |
| Budget control | None | Reserve/commit per iteration |
| Human escalation | None | Pages security lead for CRITICAL |
| Team notification | None | Summary after each iteration |
| Crash recovery | Reviews same PRs again | Resumes from last PR |
| Audit trail | None | Start, scan, verdict per PR |
| Cost | — | **$0** extra (same gh + Claude calls) |

## Run Both

```bash
# Raw (findings go to stdout):
PYTHONPATH=. GITHUB_REPO=makingscience-awake/forgeos \
  python3 examples/codebase-guardian/agent_raw.py

# Governed (15 controls, HITL for critical):
PYTHONPATH=. GITHUB_REPO=makingscience-awake/forgeos \
  ATLAS_GATEWAY_URL=... ATLAS_GATEWAY_KEY=... \
  python3 examples/codebase-guardian/agent.py
```

## Runtime Controls Across All Agents

| Agent | Raw | Governed | Per-Day | Key Feature |
|-------|-----|----------|---------|-------------|
| **SRE GCP Auditor** | 0 | **10** + 2/tool | ~182 | HITL + daily notification |
| **Content Ops Pipeline** | 0 | **12**/piece | ~12/piece | Client namespace isolation |
| **Drive Security Auditor** | 0 | **28** in 7 phases | ~28 | Capability TTL + per-user boundary |
| **Codebase Guardian** | 0 | **15**/iteration | ~2,300 | HITL for security findings |
