# Capability Coverage Matrix — SRE Command Center

Every Helios OS capability exercised by which agent in which scene.

## Kernel Capabilities

| Capability | Agent | Scene | How |
|---|---|---|---|
| Permissions (tool ACL) | Remediation Agent | 4 | `kubectl_delete`, `DROP_TABLE`, `rm_rf` in denied list |
| Budgets (daily) | All agents | All | Per-agent daily_usd limits ($2-$15) |
| Budgets (per-task) | Incident Analyst | 3 | $2/task cap on Opus analysis |
| Policies (declarative) | Deploy Guardian | 5 | `no_deploy_during_incident` blocks deploy during P0 |
| Policies (declarative) | Remediation Agent | 4 | `max_remediation_actions` caps at 3 per incident |
| Data boundaries | Incident Analyst | 3 | Blocked from `customer-data` namespace |
| Data boundaries | Code Reviewer | 5 | Blocked from `infrastructure` namespace |
| PII policy | Incident Analyst | 3 | `pii_policy: redact` on observability data |
| Capabilities (tokens) | SRE Lead → Analyst | 3 | Temporary production log access with TTL |
| Admission | All | 1 | All 6 manifests validated before deploy |

## Runtime Capabilities

| Method | Agent | Scene | How |
|---|---|---|---|
| `check_tool()` | All | All | Every tool call proactively checked |
| `budget()` | Incident Analyst | 3 | Check remaining budget before Opus call |
| `reserve()` | Incident Analyst | 3 | Reserve $1.50 before expensive analysis |
| `commit()` | Incident Analyst | 3 | Commit actual cost after analysis |
| `release()` | Incident Analyst | 3 | Release reservation on error |
| `checkpoint()` | Incident Analyst | 3 | Save investigation progress between phases |
| `last_checkpoint()` | Incident Analyst | 3 | Resume after SIGTERM or crash |
| `pending_signals()` | Incident Analyst | 3 | Check for SIGTERM between phases |
| `signal()` | SRE Lead → Analyst | 3 | SIGTERM to redirect to higher-priority P0 |
| `request_capability()` | Incident Analyst | 3 | Request temporary production log access |
| `revoke_capability()` | SRE Lead | 7 | Revoke production access after incident |
| `ask_human()` | Remediation Agent | 4 | On-call approval for kubectl_restart |
| `ask_human()` | Deploy Guardian | 6 | Tech-lead approval for production deploy |
| `notify_human()` | Alert Sentinel | 2 | P0 alert to on-call engineer |
| `notify_human()` | SRE Lead | 7 | Post-incident notification to commander |
| `contract()` | Remediation Agent | 4 | Check max actions per incident |
| `process()` | Incident Analyst | 3 | Check own resource usage mid-investigation |
| `audit()` | All | All | Every action logged to hash-chained trail |

## A2A (Agent-to-Agent)

| Pattern | From → To | Scene | How |
|---|---|---|---|
| Sync call | Sentinel → Analyst | 2 | Trigger incident investigation |
| Sync call | Reviewer → Guardian | 5 | Check deployment impact of PR |
| Sync call | Guardian → Sentinel | 5 | Check for active incidents before deploy |
| Async call | SRE Lead → Analyst | 3 | Queue research request |
| ACLs (supervisor) | SRE Lead → all | All | canCall: all 5 workers |
| ACLs (workers) | Workers → SRE Lead only | All | canBeCalledBy: [sre-lead] |
| Isolation | Analyst | 3 | Fresh context per investigation |

## A2H (Agent-to-Human)

| Pattern | Agent → Human | Scene | How |
|---|---|---|---|
| ask_human (approval) | Remediation → on-call | 4 | Approve kubectl_restart |
| ask_human (approval) | Guardian → tech-lead | 6 | Approve production deploy |
| ask_human (override) | Guardian → tech-lead | 5 | Override deploy freeze for hotfix |
| notify_human (alert) | Sentinel → on-call | 2 | P0 incident notification |
| notify_human (status) | SRE Lead → commander | 7 | P0 resolved notification |

## Multi-Platform

| Stack | Agents | Why This Stack |
|---|---|---|
| Google ADK | Alert Sentinel, Deploy Guardian | Google Search grounding, code execution for tests |
| Anthropic Agent SDK | Incident Analyst, SRE Lead | Deep reasoning (Opus), PreToolUse hook for kernel gate |
| Helios OS native | Remediation Agent, Code Reviewer | Tool denial enforcement, event-driven PR triggers |

## Multi-Model

| Model | Agent | Why This Model |
|---|---|---|
| Gemini 2.0 Flash | Alert Sentinel | Cheap, fast — runs continuously 24/7 |
| Claude Opus | Incident Analyst | Deep reasoning for root cause analysis |
| Claude Sonnet | Remediation Agent | Fast, reliable for infrastructure commands |
| GPT-4o | Code Reviewer | Strong code understanding and review |
| Gemini 2.5 Pro | Deploy Guardian | Code execution for test suites |
| Claude Opus | SRE Lead | Strategic reasoning for incident coordination |

## Execution Types

| Type | Agent | How |
|---|---|---|
| always_on | Alert Sentinel | Monitors alerts every 30 seconds |
| autonomous | Incident Analyst | Multi-phase investigation with checkpoints |
| scheduled | (Pipeline report in sales example) | — |
| event_driven | Code Reviewer | Triggers on pr.opened / pr.updated events |
| reflex | Remediation, Guardian, SRE Lead | On-demand invocation |
