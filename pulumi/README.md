# Helios OS Pulumi (GCP)

Pulumi project that provisions Helios OS on GCP — Cloud Run for the control plane
(API + dashboard + MCP), an always-on GKE Autopilot worker tier for the durable
runtime plus a `kubectl exec` sandbox, and Cloud SQL + Memorystore + Pub/Sub for
data.

**Region:** `europe-west1` · **State:** GCS bucket per project.

> **Lean stack.** Agents run in-process in the platform-api / worker per-turn
> runtime, so this stack does **not** provision per-agent pods (KEDA + per-agent
> namespaces + PodMonitoring) or the standalone Mission Control service. The
> per-agent-pod model still lives in the **local** target (`Pulumi.local.yaml` →
> `local_stack.py`).

## Environments

| Stack | GCP Project | Purpose |
|-------|-------------|---------|
| `poc` | `admachina-atomic-test-84` | Legacy proof-of-concept (existing encrypted secrets) |
| `dev` | `ms-awake-dev` | Development — permissive kernel, small SQL tier |
| `pre` | `ms-awake-pre` | Pre-production — mirrors prod config, no license gate |
| `pro` | `ms-awake-pro` | Production — `kernel_mode=production`, deletion protection on SQL |

Each environment has its own `Pulumi.<stack>.yaml` config file. CIDR ranges are
non-overlapping so VPC peering between environments is possible in the future.

### Migrating the POC stack

The `poc` stack (formerly `dev`) references the existing `admachina-atomic-test-84`
project. Since the Artifact Registry component was changed from a reference to a
managed resource, you must import the existing repo before running `pulumi up`:

```bash
pulumi stack select poc
pulumi import gcp:artifactregistry/repository:Repository forgeos-repo \
  projects/admachina-atomic-test-84/locations/europe-west1/repositories/forgeos
```

## Components

| # | Module | Resources |
|---|---|---|
| 1 | `network.py` | VPC, subnet (with pods/services secondary ranges), Cloud NAT, Private Services Access |
| 2 | `registry.py` | Artifact Registry Docker repo (`forgeos`) — created and managed by Pulumi |
| 3 | `data.py` | Cloud SQL Postgres 15 (private IP), Memorystore Redis, Pub/Sub topic for agent triggers |
| 4 | `identity.py` | 4 GSAs (`platform-api`, `agent-runtime`, `migrations`, `mcp`) + project IAM roles |
| 5 | `secrets.py` | Secret Manager entries: `database-url`, `redis-url`, `anthropic-api-key`, `openai-api-key`, `gemini-api-key`, `slack-webhook-url`, `jira-*`, `vllm-api-key`, `api-key` (MCP) |
| 6 | `gke.py` | GKE Autopilot regional cluster, private nodes, synthesized kubeconfig + k8s Provider |
| 7 | `exec_environments.py` | `forgeos-envs` sandbox namespace + ResourceQuota + default-deny NetworkPolicy + namespaced pod/exec Role/RoleBinding to the platform-api GSA + `container.clusterViewer` IAM |
| 8 | `migrations.py` | Cloud Run Job that runs `infrastructure/database/*.sql` against Cloud SQL via Direct VPC Egress |
| 9 | `platform_api.py` | Cloud Run service for the platform API (:5000), Direct VPC Egress, secret env, public invoker |
| 10 | `worker.py` | Always-on GKE Deployment — the durable per-turn worker tier that drains the Redis queue and resumes parked (HITL) runs |
| 11 | `mcp_server.py` | Cloud Run service running the MCP server (FastMCP streamable-http) on the platform-api image, pointed at the platform API |
| 12 | `dashboard.py` | Cloud Run service for the Next.js web UI (`forgeos-dashboard` image, :3000), `FORGEOS_API_URL` → the platform API |

## Secrets management

Secrets fall into two categories:

### Infra-derived (managed automatically by Pulumi)

These are generated or derived from provisioned resources and stored in Secret Manager
automatically on every `pulumi up`:

- `database-url` — assembled from the Cloud SQL private IP + generated password
- `redis-url` — assembled from the Memorystore host
- `session-secret`, `admin-api-key`, `bootstrap-admin-password` — random, generated once and stored in Pulumi state

### External (set once via `pulumi config set --secret`)

These must be provided by the operator before the first deploy. Pulumi creates the
Secret Manager resource but leaves it versionless until you add a value. Cloud Run
services only mount secrets that have at least one version, so a missing secret is
skipped gracefully (the service still deploys; the feature that needs the key is
simply unavailable).

```bash
pulumi stack select dev

pulumi config set --secret forgeos-gcp:anthropic_api_key  sk-ant-…
pulumi config set --secret forgeos-gcp:openai_api_key     sk-…
pulumi config set --secret forgeos-gcp:gemini_api_key     AIza…
pulumi config set --secret forgeos-gcp:slack_webhook_url  https://hooks.slack.com/…
pulumi config set --secret forgeos-gcp:jira_url           https://yourorg.atlassian.net
pulumi config set --secret forgeos-gcp:jira_username      user@example.com
pulumi config set --secret forgeos-gcp:jira_api_token     …
pulumi config set --secret forgeos-gcp:mcp_api_key        …   # X-API-Key the MCP server presents
pulumi config set --secret forgeos-gcp:vllm_api_key       …
pulumi config set --secret forgeos-gcp:dashboard_password …   # /api/auth/token login
pulumi config set --secret forgeos-gcp:litellm_allycode_key …
```

After adding secrets, re-run `pulumi up` to mount them into the Cloud Run revisions.

## Initial stack setup (new environment)

Run these steps once per environment (replace `dev` with `pre` or `pro` as needed):

```bash
cd pulumi

# 1. Auth
gcloud auth application-default login
gcloud config set project ms-awake-dev

# 2. Enable required APIs
gcloud services enable \
  compute.googleapis.com \
  container.googleapis.com \
  sqladmin.googleapis.com \
  redis.googleapis.com \
  servicenetworking.googleapis.com \
  artifactregistry.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  monitoring.googleapis.com \
  logging.googleapis.com \
  cloudtrace.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com

# 3. Create the Pulumi state bucket
gsutil mb -p ms-awake-dev -l europe-west1 gs://ms-awake-dev-pulumi-state
gsutil versioning set on gs://ms-awake-dev-pulumi-state

# 4. Login to Pulumi backend + create venv
pulumi login gs://ms-awake-dev-pulumi-state
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 5. Init the stack (generates the encryptionsalt — replaces NEEDS_INIT in the yaml)
pulumi stack init dev

# 6. Set external secrets (see "Secrets management" above)
pulumi config set --secret forgeos-gcp:anthropic_api_key sk-ant-…
# … (remaining secrets)

# 7. First deploy
pulumi up
```

## CI/CD workflow (GitHub Actions)

The recommended CI/CD pattern is:

- **PR opened / updated** → `pulumi preview` runs against the target stack and posts
  the plan as a PR comment. No resources are changed.
- **PR merged to `main`** → `pulumi up --yes` runs against the target stack and applies
  the changes.

Each environment maps to a branch protection rule and a separate GCP service account
used by the GitHub Actions runner (Workload Identity Federation — no long-lived keys).

```yaml
# .github/workflows/pulumi-dev.yml (example)
on:
  push:
    branches: [main]
    paths: [pulumi/**]

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      id-token: write   # for WIF
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github
          service_account: pulumi-ci@ms-awake-dev.iam.gserviceaccount.com
      - uses: pulumi/actions@v5
        with:
          command: up
          stack-name: dev
          work-dir: pulumi
        env:
          PULUMI_BACKEND_URL: gs://ms-awake-dev-pulumi-state
```

## Day-to-day

```bash
pulumi stack select dev      # switch environment
pulumi preview               # plan
pulumi up                    # apply

# Trigger migrations after schema changes
gcloud run jobs execute forgeos-migrations --region=europe-west1

# Push a new platform-api image, then roll Cloud Run:
pulumi config set forgeos-gcp:platform_api_tag $(git rev-parse --short HEAD)
pulumi up
```

## Layout

```
pulumi/
├── Pulumi.yaml
├── Pulumi.poc.yaml          # legacy POC stack (admachina-atomic-test-84)
├── Pulumi.dev.yaml          # dev environment (ms-awake-dev)
├── Pulumi.pre.yaml          # pre-production (ms-awake-pre)
├── Pulumi.pro.yaml          # production (ms-awake-pro)
├── Pulumi.local.yaml        # forgeos:target=local (per-agent pods)
├── requirements.txt
├── __main__.py              # dual-target dispatcher
├── gcp_stack.py             # the lean GCP stack (this README)
├── local_stack.py           # per-agent pods on a local k8s cluster
└── components/
    ├── network.py
    ├── registry.py
    ├── data.py
    ├── identity.py
    ├── secrets.py
    ├── gke.py
    ├── exec_environments.py
    ├── migrations.py
    ├── platform_api.py
    ├── worker.py
    ├── mcp_server.py
    ├── dashboard.py
    └── agent_local.py        # local target only
```

## Things deliberately NOT done

- **Per-agent pod autoscaling on GCP** — agents run in-process in the worker
  tier; the KEDA / per-agent-namespace model lives in the local target only.
- **Mission Control service** — removed; superseded by the dashboard.
- **Sandbox stack** — Autopilot disallows Docker-in-Docker. Revisit with Cloud Run Jobs.
- **Custom domain + Cloud Armor** — using `*.run.app` URLs.
- **Static egress IP** — Cloud NAT uses auto-allocated IPs.
- **Cost guardrails / Budget alerts** — add per-environment budget alerts in GCP Billing.
