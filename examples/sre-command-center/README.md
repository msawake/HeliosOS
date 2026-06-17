# SRE Command Center

**What happens when your AI agent runs `kubectl delete namespace production`?**

This example deploys 6 agents across 3 frameworks and 5 LLM models, demonstrating every Helios OS capability in a scenario developers live every day: incident response, code review, and deployment governance.

---

## Architecture

```
                    ┌─────────────────────┐
                    │     SRE Lead        │
                    │ Claude SDK / Opus   │
                    │ supervisor (reflex) │
                    └──────────┬──────────┘
                               │ A2A: can call all
            ┌──────────┬───────┼───────┬──────────┐
            ▼          ▼       ▼       ▼          ▼
  ┌──────────────┐ ┌────────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐
  │Alert Sentinel│ │  Incident  │ │Remediate │ │  Code    │ │  Deploy   │
  │ADK / Flash   │ │  Analyst   │ │Helios OS / │ │ Reviewer │ │ Guardian  │
  │always_on     │ │Claude/Opus │ │ Sonnet   │ │Helios OS / │ │ADK / Pro  │
  │              │ │autonomous  │ │ reflex   │ │ GPT-4o   │ │ reflex    │
  └──────────────┘ └────────────┘ └──────────┘ │event_drv │ └───────────┘
                                                └──────────┘
  Humans: on-call-engineer, tech-lead, incident-commander
```

## Agents

| Agent | Stack | Model | Execution | Budget | Role |
|---|---|---|---|---|---|
| Alert Sentinel | ADK | Gemini 2.0 Flash | always_on | $2/day | Continuous alert monitoring |
| Incident Analyst | Anthropic Agent SDK | Claude Opus | autonomous | $10/day, $2/task | Deep root cause analysis |
| Remediation Agent | Helios OS native | Claude Sonnet | reflex | $3/day | Execute infrastructure fixes |
| Code Reviewer | Helios OS native | GPT-4o | event_driven | $4/day | PR security and quality review |
| Deploy Guardian | ADK | Gemini 2.5 Pro | reflex | $3/day | Pre-deployment validation |
| SRE Lead | Anthropic Agent SDK | Claude Opus | reflex (supervisor) | $15/day | Orchestrate incident response |

## Why Governance Matters Here

**Without Helios OS, these agents could:**
- Run `kubectl delete namespace production` (Remediation Agent)
- Execute `DROP TABLE users` during investigation (Incident Analyst)
- Deploy to production during a P0 incident (Deploy Guardian)
- Access customer PII in logs (Incident Analyst)
- Burn $500 in Opus tokens on a P4 alert (Incident Analyst)
- Run infinite remediation loops (Remediation Agent)

**With Helios OS:**
- `kubectl_delete`, `DROP_TABLE`, `rm_rf` are in the DENIED tool list
- Every remediation action requires on-call engineer approval
- Deploys are blocked during P0/P1 incidents (declarative policy)
- Customer data namespace is blocked (data boundaries)
- Per-agent daily budgets with per-task caps on Opus
- Max 3 remediation actions per incident (policy engine)

## Demo Scenes

### Scene 1: Team Deployment
```bash
forgeos deploy examples/sre-command-center/team.yaml
```
Kernel validates 6 manifests. Alert Sentinel starts always_on monitoring. Code Reviewer subscribes to PR events.

### Scene 2: P0 Incident
Alert Sentinel detects "Database connection pool exhausted" → classifies P0 → notifies on-call engineer → fires A2A to Incident Analyst.

### Scene 3: Deep Investigation
Analyst starts multi-phase investigation with checkpoints between phases. Reserves budget before Opus analysis. Requests capability token for production logs. Gets SIGTERM'd when higher-priority P0 arrives → saves checkpoint, exits gracefully. Later resumes from checkpoint.

### Scene 4: Remediation
Remediation Agent tries `kubectl_delete namespace auth` → **DENIED**. Tries `kubectl_restart deployment/auth-service` → ALLOWED → asks on-call engineer for approval → approved. Checks contract: action 2 of 3 maximum.

### Scene 5: PR Review During Incident
Developer pushes fix PR → Code Reviewer triggers (event_driven) → finds SQL injection risk → calls Deploy Guardian → deploy blocked by "no deploy during P0" policy → tech-lead overrides.

### Scene 6: Deployment
Deploy Guardian runs tests → passes → asks tech-lead for production approval → deploys → records audit event.

### Scene 7: Post-Incident
SRE Lead revokes Analyst's production capability token → reviews audit trail → notifies incident-commander that P0 is resolved.

## Denied Tools (the visceral list)

```yaml
denied:
  - platform__kubectl_delete    # Cannot delete namespaces, pods, deployments
  - platform__drop_table        # Cannot modify databases
  - platform__rm_rf             # Cannot delete server files
  - platform__kubectl_exec      # Cannot exec into running containers
```

These are blocked at the kernel level. The agent cannot bypass them regardless of what the LLM generates.

## Deploy

```bash
# Deploy the full team
forgeos deploy examples/sre-command-center/team.yaml

# Or deploy individually
forgeos deploy examples/sre-command-center/agents/alert-sentinel/manifest.yaml
```

## Platforms Used

- **Google ADK**: Alert Sentinel (Google Search grounding), Deploy Guardian (code execution for tests)
- **Anthropic Agent SDK**: Incident Analyst (deep reasoning + PreToolUse hook), SRE Lead (orchestration)
- **Helios OS native**: Remediation Agent (tool denial), Code Reviewer (event-driven PR reviews)
