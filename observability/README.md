# ForgeOS Observability

Prometheus + Grafana integration for the ForgeOS platform.

## Architecture

```
┌─────────────┐         ┌──────────────┐       ┌─────────────┐
│ forgeos-api │ ◄────── │  Prometheus  │ ────► │   Grafana   │
│  /metrics   │  scrape │   Operator   │       │  dashboards │
└─────────────┘         └──────────────┘       └─────────────┘
                                ▲
                                │ rules
                                ▼
                        ┌──────────────┐
                        │ Alertmanager │
                        └──────────────┘
                                │
                                ▼
                    ┌─────────────────────┐
                    │  Slack / PagerDuty  │
                    └─────────────────────┘
```

The FastAPI app exposes `/metrics` in standard Prometheus exposition format
(see `src/platform/metrics.py`). When `prometheus-client` is installed via
the `[observability]` extra, the endpoint returns real metric families;
otherwise it returns a stub comment so scrapers can still hit it.

## Metric inventory

Source: `src/platform/metrics.py`

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `forgeos_agents_total` | Gauge | stack | Registered agents per stack |
| `forgeos_agents_running` | Gauge | – | Currently running agents |
| `forgeos_agent_deploy_total` | Counter | stack, outcome | Agent deploy count |
| `forgeos_agent_invoke_total` | Counter | stack, outcome | Agent invocation count |
| `forgeos_agent_invoke_duration_seconds` | Histogram | stack | Invocation duration |
| `forgeos_llm_calls_total` | Counter | provider, model, outcome | LLM API call count |
| `forgeos_llm_tokens_total` | Counter | provider, model | Total tokens consumed |
| `forgeos_llm_failover_total` | Counter | from_provider, to_provider | Failover events |
| `forgeos_tool_calls_total` | Counter | tool_name, outcome | Tool execution count |
| `forgeos_tool_duration_seconds` | Histogram | tool_name | Tool execution duration |
| `forgeos_scheduler_jobs_total` | Gauge | – | Registered scheduled jobs |
| `forgeos_scheduler_lag_seconds` | Gauge | – | Max lag across all jobs |
| `forgeos_events_published_total` | Counter | event_name | Event bus publishes |
| `forgeos_approvals_pending` | Gauge | – | Pending HITL approvals |
| `forgeos_approvals_resolved_total` | Counter | outcome | HITL resolutions |
| `forgeos_tenant_cost_usd_month` | Gauge | tenant_id | MTD cost per tenant |

## Deploying

### 1. Install Prometheus Operator (once per cluster)

```bash
helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace
```

### 2. Apply ForgeOS observability manifests

```bash
kubectl apply -f deploy/k8s/base/observability/servicemonitor.yaml
kubectl apply -f deploy/k8s/base/observability/prometheusrule.yaml
```

The `release: prometheus` label on both resources must match your Prometheus
CR's `serviceMonitorSelector` / `ruleSelector`. Adjust if your helm release
name differs.

### 3. Import the Grafana dashboard

Option A — UI:
1. Open Grafana → Dashboards → New → Import
2. Upload `observability/grafana/forgeos-overview.json`
3. Pick your Prometheus data source
4. Save

Option B — ConfigMap provisioning:
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: forgeos-dashboards
  labels:
    grafana_dashboard: "1"
data:
  forgeos-overview.json: |
    <contents of observability/grafana/forgeos-overview.json>
```

### 4. Wire Alertmanager to real destinations

The `PrometheusRule` fires alerts with labels `severity: sev1|sev2|sev3`.
Add a route in your Alertmanager config:

```yaml
route:
  routes:
    - match: { severity: sev1 }
      receiver: pagerduty-critical
    - match: { severity: sev2 }
      receiver: slack-major
    - match: { severity: sev3 }
      receiver: slack-warnings
```

## Alternative: In-process alerting

If you don't run Prometheus Operator, the `src/platform/alerts.py` module
ships with a dispatcher that sends alerts directly from the audit log to
Slack/PagerDuty. Set:

```
FORGEOS_ALERT_SLACK_WEBHOOK=https://hooks.slack.com/services/...
FORGEOS_ALERT_PAGERDUTY_KEY=xxx
```

and the FastAPI app will fire alerts for known-critical audit actions
(`platform.llm_failover`, `agent.crash_loop`, `cost.monthly_exceeded`, etc.)
without needing an external monitoring stack.

## Alerts summary

| Alert | Severity | Condition | Runbook |
|-------|----------|-----------|---------|
| `ForgeOSAPIDown` | SEV1 | Scrape failures for 2 min | [api-down](runbooks/api-down.md) |
| `ForgeOSHighErrorRate` | SEV1 | > 10% invoke failures | [error-rate](runbooks/error-rate.md) |
| `ForgeOSDatabaseDown` | SEV1 | API unreachable for 3 min | [db-recovery](runbooks/db-recovery.md) |
| `ForgeOSSchedulerLag` | SEV2 | Lag > 10 min | [scheduler](runbooks/scheduler.md) |
| `ForgeOSHighLLMFailoverRate` | SEV2 | > 0.5/sec failovers | [llm-outage](runbooks/llm-outage.md) |
| `ForgeOSAutonomousCrashLoop` | SEV2 | Any agent hit max_crashes | [crash-loop](runbooks/crash-loop.md) |
| `ForgeOSApprovalBacklog` | SEV3 | > 50 pending approvals | [hitl-backlog](runbooks/hitl-backlog.md) |
| `ForgeOSHighTokenUsage` | SEV3 | > 10k tokens/sec | [cost-spike](runbooks/cost-spike.md) |
| `ForgeOSTenantCostOverage` | SEV3 | Tenant > $1000 MTD | [cost-spike](runbooks/cost-spike.md) |
| `ForgeOSSlowToolCalls` | SEV3 | Tool P99 > 30s | [tool-latency](runbooks/tool-latency.md) |
