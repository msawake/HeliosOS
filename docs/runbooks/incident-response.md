# Runbook — Incident Response

**Purpose**: Standard playbook for any SEV1/SEV2 incident on Helios OS.

## Incident severities

| Severity | Definition | Response time | Page? |
|----------|------------|---------------|-------|
| **SEV1** | Platform is down OR data integrity at risk | 15 min | Yes, any hour |
| **SEV2** | Degraded service affecting multiple tenants | 1 hour | Business hours |
| **SEV3** | Minor issue affecting one tenant OR warning | 4 hours | No |
| **SEV4** | Informational / low-priority fix | Next sprint | No |

## First 15 minutes

1. **Acknowledge the page** so you're not duplicated.
2. **Open the incident channel** in Slack (`#forgeos-incidents` or similar).
3. **Post the first status message**:
   ```
   INCIDENT DECLARED — <short title>
   Severity: SEV<N>
   Started: <time>
   Symptoms: <what you're seeing>
   Current action: investigating
   IC: <your handle>
   ```
4. **Check Grafana** (`forgeos-overview` dashboard) for obvious anomalies:
   - Agent error rate spike?
   - Scheduler lag growing?
   - Database gauge flatlined?
   - LLM failover burst?
5. **Check the audit log** for recent events:
   ```bash
   curl -s https://forgeos.example.com/api/audit?limit=50 | jq
   ```

## Diagnosis decision tree

```
Is /api/health returning 200?
├─ No  → See db-recovery.md or api-down.md
└─ Yes → Is error rate > 10%?
         ├─ Yes → See error-rate.md
         └─ No  → Is scheduler lag > 10 min?
                  ├─ Yes → See scheduler.md
                  └─ No  → Is LLM failover rate high?
                           ├─ Yes → See llm-outage.md
                           └─ No  → Check user reports / logs directly
```

## Common commands

```bash
# Pod status
kubectl get pods -n forgeos

# Recent API logs
kubectl logs -n forgeos -l app.kubernetes.io/component=api \
  --tail=200 --since=10m

# Scale up API quickly
kubectl scale deploy/forgeos-api --replicas=6 -n forgeos

# Restart all API pods
kubectl rollout restart deploy/forgeos-api -n forgeos

# Emergency stop (maintenance mode)
kubectl scale deploy/forgeos-api --replicas=0 -n forgeos

# Check HPA
kubectl get hpa -n forgeos

# Check current resource pressure
kubectl top pods -n forgeos
```

## Status updates

Post updates every 15 min during an active SEV1, every 30 min for SEV2:

```
UPDATE — <title>
Time: <time since start>
Current state: <what's happening>
Next action: <what you're doing next>
ETA: <best guess>
```

## Resolving

When the immediate symptoms are gone:

1. Post a resolution message:
   ```
   RESOLVED — <title>
   Duration: <start → now>
   Cause: <one-line root cause>
   Mitigation: <what you did>
   Follow-up: <link to incident doc>
   ```
2. Schedule a post-mortem within 3 business days.
3. Open tickets for any follow-up items found during the incident.

## Post-mortem template

Use this format within 3 business days (blameless):

```markdown
# Incident YYYY-MM-DD — <short title>

## Summary
One paragraph: what happened, who was affected, how long it lasted.

## Timeline (all times UTC)
- HH:MM — first symptom
- HH:MM — alert fired
- HH:MM — on-call paged
- HH:MM — incident declared
- HH:MM — root cause identified
- HH:MM — mitigation applied
- HH:MM — all clear

## Impact
- Tenants affected: N
- Requests failed: N
- Duration: X minutes
- Data loss: yes/no (explain)
- Cost overrun: $X (if applicable)

## Root cause
Technical explanation. No blame, focus on the failure mode.

## What went well
- ...
## What went wrong
- ...
## What was lucky
- ...

## Action items
| # | Action | Owner | Priority | Due |
|---|--------|-------|----------|-----|
| 1 | ... | @alice | P0 | YYYY-MM-DD |
| 2 | ... | @bob | P1 | YYYY-MM-DD |

## Lessons learned
- ...
```

## Severity downgrade rules

- SEV1 → SEV2: once platform is functional for all tenants but some
  degradation remains.
- SEV2 → SEV3: once the issue affects one tenant only and they're aware.
- SEV3 → Closed: once the fix is verified in production.

## Escalation

If the on-call engineer is stuck for > 30 min on a SEV1:
1. Page the platform lead.
2. Post in `#forgeos-escalation`.
3. If customer-facing, notify the customer success lead.

## Communication templates

**Customer-facing status page**:
```
INVESTIGATING — We're looking into reports of <symptom>. We'll post an
update within 15 minutes.

IDENTIFIED — We've identified <issue> affecting <scope>. Working on a fix.

MONITORING — A fix has been applied. We're monitoring for stability.

RESOLVED — The incident has been resolved. Full post-mortem to follow.
```
