# Capability Coverage Matrix

Which Helios OS capability is exercised by which agent in which demo scene.

## Legend

- **S1** Cold Start Deploy
- **S2** Lead Qualification Pipeline
- **S3** Supervised Outreach
- **S4** Budget Exhaustion and Recovery
- **S5** Cross-Namespace Data Access
- **S6** Signal-Based Task Interruption
- **S7** Morning Pipeline Brief

## Agent Abbreviations

- **MM** market-monitor
- **RA** research-analyst
- **LQ** lead-qualifier
- **OC** outreach-composer
- **PA** pipeline-analyst
- **SM** sales-manager

---

## Orchestration and Routing

| Capability | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Agents |
|---|---|---|---|---|---|---|---|---|
| Registry (deploy/discover) | x | | | | | | | all |
| Executor (dispatch/invoke) | x | x | x | x | x | x | x | all |
| Scheduler (cron lifecycle) | x | | | | | | x | PA |
| Event Bus (pub/sub) | | x | | | | | x | MM, PA, SM |
| LLM Router (multi-provider) | x | x | x | x | x | x | x | all |
| Agentic Loop (tool cycle) | | x | x | x | x | x | x | all |
| Skill Registry | | x | | | | | | RA |

## A2A Protocol

| Capability | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Agents |
|---|---|---|---|---|---|---|---|
| agent__call (sync) | | x | | | | | x | LQ->MM, PA->LQ, SM->* |
| agent__async_call | | x | | | | | | LQ->RA |
| agent__await | | x | | | | | | LQ |
| agent__list_available | | | | | | | | SM |
| ACL enforcement | | x | x | | x | | x | all callers |
| Cycle detection | | x | | | | | x | platform |
| Depth limiting | | x | | | | | x | platform |

## Process Management

| Capability | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Agents |
|---|---|---|---|---|---|---|---|
| AgentProcess creation | x | | | | | | | all |
| Phase transitions | x | x | x | x | x | x | x | all |
| PID assignment | x | | | | | | | all |
| Resource accounting | | x | x | x | x | x | x | all |
| Checkpoint save | | | | | | x | | RA |
| Checkpoint restore | | | | | | x | | RA |
| Signal: SIGTERM | | | | | | x | | SM->RA |
| Signal: SIGSTOP/SIGCONT | | | | | | x | | SM->RA |

## Admission and Governance

| Capability | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Agents |
|---|---|---|---|---|---|---|---|
| Identity check | x | x | x | x | x | x | x | all |
| Capability check | | | x | | x | | | OC, RA |
| Quota/budget admission | | x | x | x | x | x | x | all |
| Policy evaluation | | | x | | | | | OC |
| Data boundary check | | x | x | x | x | | | LQ, OC, RA |
| Audit logging | | x | x | x | x | x | x | all |

## Capabilities (Opaque Tokens)

| Capability | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Agents |
|---|---|---|---|---|---|---|---|
| Token grant | | | | | x | | | SM->RA |
| Token scoping (namespace) | | | | | x | | | RA |
| Token TTL / expiry | | | | | x | | | RA |
| Token revocation | | | | | x | | | SM |
| ACL bypass via token | | | | | x | | | RA |

## Boundaries

| Capability | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Agents |
|---|---|---|---|---|---|---|---|
| Daily budget enforcement | | x | x | x | x | x | x | all |
| Per-task budget | | x | | x | x | | | RA |
| Budget reservation | | | | x | x | | | RA |
| Namespace allow/block | | x | x | x | x | | | LQ, OC, RA |
| PII policy: mask | | | | | x | | | RA, OC |
| PII policy: detect | | x | | | | | | LQ |
| Tool allowlist | | | x | | | | | OC |
| Tool denylist | | | x | | | | | OC |

## Human-in-the-Loop

| Capability | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Agents |
|---|---|---|---|---|---|---|---|
| request_approval | | | x | | | | | OC |
| Approver routing | | | x | | | | | OC->sales-lead |
| SLA enforcement | | | x | | | | | platform |
| Escalation (supervisor) | | | | x | | x | | SM |
| Escalation (human board) | | | | x | | | | SM |

## Observability

| Capability | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Agents |
|---|---|---|---|---|---|---|---|
| Hash-chained audit trail | | x | x | x | x | x | x | all |
| Metric recording | | x | x | x | | | x | LQ, OC, RA, PA |
| Event publishing | | x | | | | | x | MM, PA, SM |
| Budget alerts | | | | x | | | | platform |

## Multi-Stack

| Capability | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Agents |
|---|---|---|---|---|---|---|---|
| Helios OS adapter | x | x | x | | | | | LQ, OC |
| ADK adapter | x | x | | | | | x | MM, PA |
| Anthropic Agent SDK | x | x | | x | x | x | | RA, SM |
| Cross-stack A2A | | x | | | | | x | LQ->MM, PA->LQ |

## Execution Lifecycles

| Capability | S1 | S2 | S3 | S4 | S5 | S6 | S7 | Agents |
|---|---|---|---|---|---|---|---|
| always_on | x | x | | | | | | MM |
| autonomous | x | x | | x | x | x | | RA |
| reflex | x | x | x | | | | | LQ, OC, SM |
| scheduled | x | | | | | | x | PA |

---

## Coverage Summary

| Category | Capabilities | Scenes Covered |
|---|---|---|
| Orchestration and Routing | 7 | S1-S7 (all) |
| A2A Protocol | 7 | S2, S7 |
| Process Management | 8 | S1, S6 |
| Admission and Governance | 6 | S1-S7 (all) |
| Capabilities (Tokens) | 5 | S5 |
| Boundaries | 7 | S2-S5 |
| Human-in-the-Loop | 5 | S3-S4, S6 |
| Observability | 4 | S2-S5, S7 |
| Multi-Stack | 4 | S1-S3, S5-S7 |
| Execution Lifecycles | 4 | S1-S2, S4-S7 |
| **Total** | **57** | **7 scenes** |

Every scene exercises at least 5 capability categories. Every capability category is covered by at least one scene.
