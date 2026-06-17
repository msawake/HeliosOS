# SRE GCP Daily Auditor

Daily read-only audit of all Google Cloud projects in the organization.
Uses **ADK** (Google Agent Development Kit) + **Helios OS HTTP Kernel** (Mode C) for governance.

## Architecture

```
┌─────────────────────────────┐     HTTP (Mode C)     ┌──────────────────────┐
│  Cloud Run: SRE Auditor     │◄─────────────────────►│  Helios OS Control   │
│                             │  check-tool, audit,    │  Plane (Cloud Run)   │
│  ADK Agent (Gemini Flash)   │  budget, checkpoint    │                      │
│  ├─ 10 gcloud read tools    │                        │  Kernel + ProcessTable│
│  ├─ Helios OS runtime (HTTP)  │                        │  + Audit Trail     │
│  └─ Daily at 6 AM UTC       │                        └──────────────────────┘
└─────────────────────────────┘
         │ (read-only)
   All GCP Projects (org-level viewer SA)
```

## Prerequisites

1. **Service Account** with org-level read access:
   ```bash
   ORG_ID=$(gcloud organizations list --format="value(ID)")

   gcloud iam service-accounts create forgeos-sre-auditor \
     --display-name="Helios OS SRE Daily Auditor" \
     --project=YOUR_PROJECT

   # Read-only roles
   gcloud organizations add-iam-policy-binding $ORG_ID \
     --member="serviceAccount:forgeos-sre-auditor@YOUR_PROJECT.iam.gserviceaccount.com" \
     --role="roles/viewer"
   gcloud organizations add-iam-policy-binding $ORG_ID \
     --member="serviceAccount:forgeos-sre-auditor@YOUR_PROJECT.iam.gserviceaccount.com" \
     --role="roles/billing.viewer"
   gcloud organizations add-iam-policy-binding $ORG_ID \
     --member="serviceAccount:forgeos-sre-auditor@YOUR_PROJECT.iam.gserviceaccount.com" \
     --role="roles/iam.securityReviewer"
   ```

2. **gcloud CLI** authenticated (or running on Cloud Run with the service account attached)

3. **Python packages**:
   ```bash
   pip install 'google-adk>=1.29' forgeos-sdk
   ```

## Usage

### Local (no governance)
```bash
python3 examples/sre-gcp-auditor/agent.py
```

### With Helios OS HTTP Kernel (Mode C)
```bash
FORGEOS_API_URL=https://forgeos-api-xxx.run.app \
FORGEOS_AGENT_ID=sre-gcp-auditor \
GOOGLE_API_KEY=AIza... \
python3 examples/sre-gcp-auditor/agent.py
```

### Deploy to Cloud Run
```bash
gcloud run deploy sre-gcp-auditor \
  --source=. \
  --service-account=forgeos-sre-auditor@YOUR_PROJECT.iam.gserviceaccount.com \
  --set-env-vars="FORGEOS_API_URL=https://forgeos-api-xxx.run.app,FORGEOS_AGENT_ID=sre-gcp-auditor" \
  --no-allow-unauthenticated
```

## What Gets Audited

| Area | Tools | Flags |
|------|-------|-------|
| **Infrastructure** | list_cloud_run_services, list_gke_clusters, list_cloud_sql_instances | Unhealthy services, outdated versions, missing backups |
| **Security** | list_firewall_rules, list_service_accounts, list_storage_buckets, list_secrets, list_iam_bindings | Public IPs, 0.0.0.0/0 rules, external members, unrotated secrets |
| **Billing** | get_billing_info | Budget overruns, cost anomalies |

## Governance (Helios OS Kernel)

Every gcloud tool call is gated by the kernel via `runtime.check_tool()`:
- Manifest allowlists 10 read-only tools
- Write operations (`create_*`, `delete_*`, `update_*`, `bash.*`) are explicitly denied
- $3/day budget limit, $0.30 per project
- Critical findings trigger `runtime.ask_human()` for on-call escalation
- Full audit trail of every tool call and finding

## Runtime Governance Calls Per Audit

| # | Call | Purpose |
|---|------|---------|
| 1 | `pending_signals()` | Check for drain/quarantine |
| 2 | `budget()` | Enough budget for today? |
| 3 | `process()` | Am I still RUNNING? |
| 4-N | `check_tool("gcp.*")` | Kernel gate per gcloud command |
| N+1 | `audit("daily_audit.completed")` | Record results |
| N+2 | `checkpoint({date, findings})` | Save for crash recovery |
