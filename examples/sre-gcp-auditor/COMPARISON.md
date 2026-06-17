# SRE GCP Auditor: Raw vs Helios OS Governed

Two files, same audit, completely different operational readiness.

```
agent_raw.py  →  77 lines,  0 runtime checks, findings go nowhere
agent.py      → 626 lines, 10 runtime checks, critical findings page on-call
```

## Side-by-Side: The Core Audit

### WITHOUT Helios OS (`agent_raw.py`)

```python
async def run_audit():

    # ── List projects ──
    projects = gcp_tools.list_projects().get("items", [])

    # ── Audit each project ──
    for proj in projects:
        pid = proj.get("projectId", "unknown")

        services = gcp_tools.list_cloud_run_services(pid)
        sql      = gcp_tools.list_cloud_sql_instances(pid)
        firewalls = gcp_tools.list_firewall_rules(pid)
        iam      = gcp_tools.list_iam_bindings(pid)
        sas      = gcp_tools.list_service_accounts(pid)
        buckets  = gcp_tools.list_storage_buckets(pid)
        secrets  = gcp_tools.list_secrets(pid)

        print(f"  Firewalls: {len(firewalls.get('items', []))}")

    print("Done. Report exists only in stdout.")
```

**That's it.** Finds a public database with no authorized networks, an SSH port open to the internet, and a SENDGRID API key exposed in env vars — prints to stdout and exits. Nobody is notified. The findings sit in a terminal that closes.

---

### WITH Helios OS (`agent.py`)

```python
async def run_audit():

    # ① Should I stop? (signals from orchestrator)
    signals = await runtime.pending_signals()
    if signals:
        await runtime.audit("agent.draining", {"signals": signals})
        break

    # ② Can I afford this audit?
    budget = await runtime.budget()
    if budget.remaining_usd < 0.10:
        await runtime.audit("agent.budget_paused", {"remaining": budget.remaining_usd})
        break

    # ③ Am I still allowed to run?
    proc = await runtime.process()
    if proc.phase in ("quarantined", "evicted", "draining"):
        break

    # ⑧ Do I have permission to read GCP project data?
    data_decision = await runtime.check_data("gcp-projects")
    if data_decision.action == "deny":
        break

    # ⑨ Reserve budget before spending
    budget_ticket = await runtime.reserve(estimated_cost_usd=1.00)

    # ── ADK Agent runs the audit ──
    # (each gcloud tool call gated by runtime.check_tool() inside wrapper)
    audit_result = await run_adk_agent(...)

    # ④ Record what was found
    await runtime.audit("daily_audit.completed", {
        "date": today, "tool_calls": 86, "duration_ms": elapsed
    })

    # ⑩ Finalize budget with actual cost
    await runtime.commit(budget_ticket, actual_cost_usd=actual_cost)

    # ⑤ Save progress (crash at project 8 → resume from 8, not 1)
    await runtime.checkpoint({"iteration": iteration, "last_audit_date": today})

    # ⑥ CRITICAL findings? Page the on-call immediately.
    if has_critical:
        await runtime.ask_human(
            namespace="ops",
            name="security-oncall",
            question="🔴 CRITICAL: forgeos-db has public IP, SSH open to 0.0.0.0/0",
            response_type="choice",
            options=[
                {"value": "ack",      "label": "Acknowledge — working on it"},
                {"value": "escalate", "label": "Escalate to VP Engineering"},
                {"value": "defer",    "label": "Defer to next business day"},
            ],
            priority="critical",
        )

    # ⑦ Send daily summary to the team
    await runtime.notify_human(
        namespace="ops",
        name="sre-team",
        message=f"GCP Audit — {today}\n🔴 CRITICAL findings — check report",
        priority="high" if has_critical else "low",
    )
```

---

## What Each Check Prevents

| # | Runtime Call | Without It | Real Example From Our Audit |
|---|------------|------------|---------------------------|
| ① | `pending_signals()` | Agent ignores shutdown commands | Agent keeps scanning after incident resolved |
| ② | `budget()` | Unlimited spending | 10-project scan with Gemini costs $50 instead of $3 |
| ③ | `process()` | Runs when quarantined | Agent flagged as misbehaving still accesses prod |
| ⑧ | `check_data()` | Reads any project | Agent scans HR project with salary data in metadata |
| ⑨ | `reserve()` | Cost unknown upfront | Starts $50 audit with $2 remaining — fails halfway |
| per-tool | `check_tool()` | Any gcloud command | Agent could run `gcloud compute instances delete` |
| per-tool | `audit()` | No tool call log | "Which projects did you scan?" — no record |
| ④ | `audit()` | No completion record | "Did the audit run yesterday?" — nobody knows |
| ⑩ | `commit()` | Budget not reconciled | Monthly cost report shows $0 for SRE auditing |
| ⑤ | `checkpoint()` | Crash = full restart | Rate-limited at project 8 → restarts from 1, double cost |
| ⑥ | `ask_human()` | Critical findings ignored | **Public DB IP found May 18 — still exposed June 1** |
| ⑦ | `notify_human()` | Team unaware audit ran | Security review: "Do you audit GCP daily?" — "I think so?" |

## The Real Finding That Proves the Point

On 2026-05-18, the SRE auditor found across 10 projects:

```
🔴 CRITICAL (7 findings):
  - forgeos-db has public IP (34.38.89.202) — no authorized networks
  - rainman-poc-db has public IP (104.197.57.192)
  - SSH open to 0.0.0.0/0 (allow-ssh-gpustack, port 22)
  - SENDGRID_API_KEY exposed in Cloud Run env vars
  - ANTHROPIC_API_KEY hardcoded in Cloud Run env vars
  - OPENAI_API_KEY hardcoded in Cloud Run env vars
  - 18 firewall rules allow 0.0.0.0/0 on ports 80, 8080, 3000
```

**With `agent_raw.py`**: These findings printed to stdout. Nobody saw them.

**With `agent.py`**: Check ⑥ (`ask_human`) pages security-oncall with the finding list. Check ⑦ (`notify_human`) sends the daily summary to the SRE team. The audit trail (④) proves the finding was detected on May 18. If the DB is still public on June 1, the audit record shows the team was notified and chose to defer.

## Run Both

```bash
# Raw (no governance — findings go nowhere):
PYTHONPATH=. python3 examples/sre-gcp-auditor/agent_raw.py

# Governed (10 checks — critical findings page on-call):
PYTHONPATH=. ATLAS_GATEWAY_URL=... ATLAS_GATEWAY_KEY=... \
  python3 examples/sre-gcp-auditor/agent.py
```

## The Numbers

|  | Raw | Helios OS Governed |
|--|-----|-----------------|
| Lines of code | 77 | 626 |
| Loop-level checks | 0 | 10 |
| Per-tool checks | 0 | 2 per call (check + audit) |
| Total checks (10 projects) | 0 | **10 + 172 = 182** |
| Client isolation | None | Namespace boundary |
| Budget control | None | Reserve/commit per audit |
| Human escalation | None | Pages on-call for CRITICAL |
| Team notification | None | Daily summary to SRE team |
| Crash recovery | Start over | Resume from checkpoint |
| Audit trail | None | Every tool call + findings |
| Cost to add | — | **$0** (same gcloud calls) |

**The gcloud commands are identical.** Helios OS wraps governance around them — the agent finds the same things, but now someone actually sees the findings and acts on them.
