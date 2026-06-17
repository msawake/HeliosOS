# Helios OS Pulumi (GCP)

Pulumi project that provisions Helios OS on GCP — Cloud Run for the control plane
(API + dashboard + MCP), an always-on GKE Autopilot worker tier for the durable
runtime plus a `kubectl exec` sandbox, and Cloud SQL + Memorystore + Pub/Sub for
data.

**Project:** `admachina-atomic-test-84` · **Region:** `europe-west1` · **State:** GCS bucket in-project.

> **Lean stack.** Agents run in-process in the platform-api / worker per-turn
> runtime, so this stack does **not** provision per-agent pods (KEDA + per-agent
> namespaces + PodMonitoring) or the standalone Mission Control service. The
> per-agent-pod model still lives in the **local** target (`Pulumi.local.yaml` →
> `local_stack.py`).

## Components

| # | Module | Resources |
|---|---|---|
| 1 | `network.py` | VPC, subnet (with pods/services secondary ranges), Cloud NAT, Private Services Access |
| 2 | `registry.py` | Artifact Registry Docker repo (`forgeos`) |
| 3 | `data.py` | Cloud SQL Postgres 15 (private IP), Memorystore Redis, Pub/Sub topic for agent triggers |
| 4 | `identity.py` | 4 GSAs (`platform-api`, `agent-runtime`, `migrations`, `mcp`) + project IAM roles |
| 5 | `secrets.py` | Secret Manager entries: `database-url`, `redis-url`, `anthropic-api-key`, `openai-api-key`, `gemini-api-key`, `slack-webhook-url`, `jira-*`, `vllm-api-key`, `api-key` (MCP) |
| 6 | `gke.py` | GKE Autopilot regional cluster, private nodes, synthesized kubeconfig + k8s Provider |
| 7 | `exec_environments.py` | `forgeos-envs` sandbox namespace + ResourceQuota + default-deny NetworkPolicy + namespaced pod/exec Role/RoleBinding to the platform-api GSA + `container.clusterViewer` IAM |
| 8 | `migrations.py` | Cloud Run Job that runs `infrastructure/database/*.sql` against Cloud SQL via Direct VPC Egress |
| 9 | `platform_api.py` | Cloud Run service for FastAPI (:5000), Direct VPC Egress, secret env, public invoker |
| 10 | `worker.py` | Always-on GKE Deployment — the durable per-turn worker tier that drains the Redis queue and resumes parked (HITL) runs |
| 11 | `mcp_server.py` | Cloud Run service running the MCP server (FastMCP streamable-http) on the platform-api image, pointed at the platform API |
| 12 | `dashboard.py` | Cloud Run service for the Next.js web UI (`forgeos-dashboard` image, :3000), `FORGEOS_API_URL` → the platform API |

## One-time setup

```bash
cd pulumi

# Auth (uses your local gcloud creds)
gcloud auth application-default login
gcloud config set project admachina-atomic-test-84

# Enable required APIs
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

# State bucket (one-time)
gsutil mb -p admachina-atomic-test-84 -l europe-west1 \
  gs://admachina-atomic-test-84-pulumi-state
gsutil versioning set on gs://admachina-atomic-test-84-pulumi-state

# Pulumi login + venv
pulumi login gs://admachina-atomic-test-84-pulumi-state
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Configure secrets (one-time, optional)

If you skip these, Pulumi still creates the Secret Manager resources — you can
add a version later with `gcloud secrets versions add forgeos-anthropic-api-key --data-file=…`.

```bash
pulumi stack select dev    # creates on first run

pulumi config set --secret forgeos-gcp:anthropic_api_key sk-ant-…
pulumi config set --secret forgeos-gcp:openai_api_key    sk-…
pulumi config set --secret forgeos-gcp:slack_webhook_url https://hooks.slack.com/…
pulumi config set --secret forgeos-gcp:mcp_api_key       …   # X-API-Key the MCP server presents
```

## Agents

On GCP, agents run **in-process** in the platform-api / worker per-turn runtime
(enqueued to Redis, drained by the always-on worker) — there are no per-agent
pods to declare here. To run agents as individual pods (per-agent Deployment +
Service + KEDA scale-to-zero), use the **local** target:

```bash
pulumi stack select local   # forgeos:target=local → local_stack.py
```

See `Pulumi.local.yaml` for the per-agent declaration format.

## Day-to-day

```bash
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
├── Pulumi.dev.yaml          # forgeos:target=gcp  (default)
├── Pulumi.local.yaml        # forgeos:target=local
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

## Things deliberately NOT done in v1

- **Per-agent pod autoscaling on GCP** — agents run in-process in the worker
  tier; the KEDA / per-agent-namespace model lives in the local target only.
- **Mission Control service** — removed; superseded by the dashboard, which is
  now managed by this stack (`dashboard.py`).
- **Sandbox stack** — Autopilot disallows Docker-in-Docker. Revisit with Cloud Run Jobs.
- **Custom domain + Cloud Armor** — using `*.run.app` URLs.
- **Static egress IP** — Cloud NAT uses auto-allocated IPs.
- **Cost guardrails / Budget alerts** — test project, low blast radius.
- **dev/staging/prod split** — single stack against a single project.
