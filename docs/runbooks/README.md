# Helios OS Runbooks

Operational playbooks for Helios OS on-call. Each runbook corresponds to a
specific alert or failure mode and follows the same structure:

1. **Symptoms** — how you'll know
2. **Immediate triage** — check-commands to confirm scope
3. **Recovery paths** — ordered by likelihood
4. **Data loss estimation** — RPO/RTO context
5. **Post-incident** — follow-up tasks

## Catalog

| Runbook | Trigger | Severity |
|---------|---------|----------|
| [incident-response.md](incident-response.md) | Any SEV1/SEV2 | — |
| [db-recovery.md](db-recovery.md) | `ForgeOSDatabaseDown` | SEV1 |
| [api-down.md](api-down.md) | `ForgeOSAPIDown` | SEV1 |
| [error-rate.md](error-rate.md) | `ForgeOSHighErrorRate` | SEV1 |
| [llm-outage.md](llm-outage.md) | `ForgeOSHighLLMFailoverRate` | SEV2 |
| [scheduler.md](scheduler.md) | `ForgeOSSchedulerLag` | SEV2 |
| [crash-loop.md](crash-loop.md) | `ForgeOSAutonomousCrashLoop` | SEV2 |
| [hitl-backlog.md](hitl-backlog.md) | `ForgeOSApprovalBacklog` | SEV3 |
| [cost-spike.md](cost-spike.md) | `ForgeOSHighTokenUsage` / `ForgeOSTenantCostOverage` | SEV3 |
| [tool-latency.md](tool-latency.md) | `ForgeOSSlowToolCalls` | SEV3 |

## Principles

- **Every alert links to a runbook.** If an alert fires without a runbook,
  write one before closing the incident.
- **Runbooks are prescriptive**, not descriptive. Say *what to do*, not
  *what went wrong*.
- **Test runbooks quarterly** by running a game day with the chaos
  experiments in `deploy/k8s/chaos/`.
- **Keep commands copy-paste-able.** On-call at 2am doesn't want to
  adapt snippets.

## When a new alert is added

1. Add the alert rule to `deploy/k8s/base/observability/prometheusrule.yaml`.
2. Add the Alertmanager label/severity.
3. Write a runbook in this directory.
4. Link it from the PrometheusRule `runbook_url` annotation.
5. Update this catalog.
