# SRE Command Center: Raw vs ForgeOS Governed

```
agent_raw.py  →  70 lines,  0 controls, kubectl delete SUCCEEDS
agent.py      → 550 lines, 35 controls, kubectl delete BLOCKED by kernel
```

## The Killer Scenario

The Remediation Agent decides to fix a database connection pool issue by deleting the auth namespace:

**Without ForgeOS:**
```
kubectl delete namespace auth → DELETED
DROP TABLE users             → DROPPED
deploy during P0             → DEPLOYED
```
Production destroyed. Nobody approved. No record it happened.

**With ForgeOS:**
```
kubectl delete namespace auth → ✗ DENIED by kernel (tool in denied list)
DROP TABLE users              → ✗ DENIED by kernel (tool in denied list)
kubectl exec -it pod -- bash  → ✗ DENIED by kernel (tool in denied list)
kubectl restart auth-service  → ✓ ALLOWED, but requires human approval
  → ask_human('on-call-engineer') → APPROVED
  → audit('remediation.executed') → recorded
  → action 1 of 3 maximum (policy enforced)
```

## 35 Controls Across 7 Scenes

| # | Scene | Runtime Call | What It Prevents |
|---|-------|------------|-----------------|
| ① | Alert | `pending_signals()` | Sentinel runs after shutdown |
| ② | Alert | `check_tool('query_alerts')` | Unauthorized alert access |
| ③ | Alert | `notify_human('on-call')` | P0 detected, nobody paged |
| ④ | Alert | `check_a2a('incident-analyst')` | Cross-agent call without permission |
| ⑤ | Alert | `audit('alert.p0_detected')` | No record of the alert |
| ⑥ | Investigate | `last_checkpoint()` | Crash = restart from scratch |
| ⑦ | Investigate | `budget()` | Opus burns $50 on a P4 alert |
| ⑧ | Investigate | `reserve($2.00)` | Cost unknown until invoice |
| ⑨ | Investigate | `check_data('observability')` | Accesses wrong namespace |
| ⑩ | Investigate | `check_data('customer-data')` | **Reads customer PII in logs** |
| ⑪ | Investigate | `request_capability(ttl=1800)` | Permanent access to prod logs |
| ⑫ | Investigate | `process()` | Can't see own resource usage |
| ⑬ | Investigate | `check_tool('query_logs')` | Unauthorized log access |
| ⑭ | Investigate | `checkpoint(phase=1)` | Crash after logs = redo |
| ⑮ | Investigate | `pending_signals()` | Ignores redirect to higher-priority P0 |
| ⑯ | Investigate | `checkpoint(phase=3)` | Crash after traces = redo |
| ⑰ | Investigate | `commit(ticket, $1.20)` | Budget not reconciled |
| ⑱ | Investigate | `audit('rca_identified')` | No record of root cause |
| ⑲ | Remediate | `contract()` | Doesn't know max 3 actions |
| ⑳ | Remediate | `check_tool('kubectl_delete')` | **DELETES PRODUCTION NAMESPACE** |
| ㉑ | Remediate | `check_tool('drop_table')` | **DROPS USER TABLE** |
| ㉒ | Remediate | `check_tool('kubectl_exec')` | **Execs into prod containers** |
| ㉓ | Remediate | `check_tool('kubectl_restart')` | Allows safe action |
| ㉔ | Remediate | `ask_human('on-call')` | **Restarts without approval** |
| ㉕ | Remediate | `audit('remediation.executed')` | No record of what was done |
| ㉖ | PR Review | `check_tool('read_pr_diff')` | Reviews unauthorized repos |
| ㉗ | PR Review | `audit('pr.review_started')` | No review record |
| ㉘ | PR Review | `check_a2a('deploy-guardian')` | Calls wrong agent |
| ㉙ | PR Review | `check_tool('check_incidents')` | Doesn't check for P0 |
| ㉚ | PR Review | `audit('deploy.blocked')` | **Deploys during P0 incident** |
| ㉛ | Deploy | `check_tool('run_tests')` | Deploys without tests |
| ㉜ | Deploy | `check_tool('deploy_staging')` | Skips staging |
| ㉝ | Deploy | `ask_human('tech-lead')` | **Deploys without approval** |
| ㉞ | Deploy | `audit('deploy.production')` | No deploy record |
| ㉟ | Cleanup | `revoke_capability(token)` | **Analyst keeps prod access forever** |
| ㊱ | Cleanup | `audit('incident.resolved')` | No incident record |
| ㊲ | Cleanup | `notify_human('commander')` | Commander doesn't know it's resolved |
| ㊳ | Cleanup | `checkpoint(resolved)` | No final state saved |

## The Numbers

|  | Raw | ForgeOS Governed |
|--|-----|-----------------|
| Lines of code | 70 | 550 |
| Runtime controls | 0 | **35** across 7 scenes |
| Dangerous tools blocked | 0 | **3** (delete, drop, exec) |
| Human approvals | 0 | **2** (remediation + deploy) |
| Capability tokens | 0 issued, 0 revoked | **1** issued with TTL, **1** revoked |
| Budget tracking | None | Reserve → commit with actual cost |
| Crash recovery | Start over | **3** checkpoints across phases |
| Audit records | 0 | **10+** hash-chained entries |
| Humans notified | 0 | **3** (on-call, tech-lead, commander) |
| Data boundaries | None | customer-data **BLOCKED** |
| Deploy during P0 | Happens | **BLOCKED by policy** |

## Run Both

```bash
# Raw (production destroyed):
PYTHONPATH=. python3 examples/sre-command-center/agent_raw.py

# Governed (35 controls, 3 blocks, 2 approvals):
PYTHONPATH=. python3 examples/sre-command-center/agent.py
```

## Runtime Controls Across All Agents

| Agent | Controls | Denied Tools | HITL | Key Feature |
|-------|----------|-------------|------|-------------|
| SRE GCP Auditor | 10 + 2/tool | 0 | 2 | Daily infra audit |
| Content Ops | 12/piece | 0 | 1 | Client namespace isolation |
| Drive Security | 28 in 7 phases | 0 | 2 | Capability TTL + per-user boundary |
| Codebase Guardian | 15/iteration | 0 | 1 | PR security scanning |
| **SRE Command Center** | **35 across 7 scenes** | **3** | **2** | **Full incident lifecycle governance** |
