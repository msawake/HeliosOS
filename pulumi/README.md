# ForgeOS Pulumi (GCP)

Pulumi project that provisions ForgeOS on GCP — Cloud Run for the control plane,
GKE Autopilot for agent workloads, Cloud SQL + Memorystore + Pub/Sub for data.

**Project:** `admachina-atomic-test-84` · **Region:** `europe-west1` · **State:** GCS bucket in-project.

## Components

| # | Module | Resources |
|---|---|---|
| 1 | `network.py` | VPC, subnet (with pods/services secondary ranges), Cloud NAT, Private Services Access |
| 2 | `registry.py` | Artifact Registry Docker repo (`forgeos`) |
| 3 | `data.py` | Cloud SQL Postgres 15 (private IP), Memorystore Redis, Pub/Sub topic for agent triggers |
| 4 | `identity.py` | 4 GSAs (`platform-api`, `mc`, `agent-runtime`, `migrations`) + project IAM roles |
| 5 | `secrets.py` | Secret Manager entries: `database-url`, `redis-url`, `anthropic-api-key`, `openai-api-key`, `mc-admin-password`, `slack-webhook-url` |
| 6 | `gke.py` | GKE Autopilot regional cluster, private nodes, synthesized kubeconfig + k8s Provider |
| 7 | `keda.py` | KEDA Helm release in `keda` namespace |
| 8 | `namespaces.py` | One k8s namespace per ForgeOS namespace + `forgeos-agent` KSA (WI-bound) + ResourceQuota + default-deny NetworkPolicy |
| 9 | `migrations.py` | Cloud Run Job that runs `infrastructure/database/*.sql` against Cloud SQL via Direct VPC Egress |
| 10 | `platform_api.py` | Cloud Run service for FastAPI :5099, Direct VPC Egress, secret env, public invoker |
| 11 | `mission_control.py` | Cloud Run service for FastAPI :8888 + bundled SPA, env points at platform API |
| 12 | `agent_base.py` | Per-agent Deployment + per-agent Pub/Sub subscription + KEDA ScaledObject (0→N on backlog) |
| 13 | `observability.py` | PodMonitoring CR per namespace for Google Managed Service for Prometheus |

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
pulumi config set --secret forgeos-gcp:mc_admin_password $(openssl rand -base64 24)
pulumi config set --secret forgeos-gcp:slack_webhook_url https://hooks.slack.com/…
```

## Declaring agents (optional)

Edit `Pulumi.dev.yaml`:

```yaml
config:
  forgeos-gcp:agents:
    - name: lead-qualifier
      namespace: sales-team
      manifest_ref: gs://forgeos-manifests/lead-qualifier.yaml
      always_on: true
      max_replicas: 5
    - name: invoice-watcher
      namespace: operations
      manifest_ref: gs://forgeos-manifests/invoice-watcher.yaml
      always_on: false     # scaled to 0, woken by Pub/Sub
      max_replicas: 20
```

Agents inherit `cpu=250m` / `memory=512Mi` unless overridden per entry.

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
├── Pulumi.dev.yaml
├── requirements.txt
├── __main__.py
└── components/
    ├── network.py
    ├── registry.py
    ├── data.py
    ├── identity.py
    ├── secrets.py
    ├── gke.py
    ├── keda.py
    ├── namespaces.py
    ├── migrations.py
    ├── platform_api.py
    ├── mission_control.py
    ├── agent_base.py
    └── observability.py
```

## Things deliberately NOT done in v1

- **Sandbox stack** — Autopilot disallows Docker-in-Docker. Revisit with Cloud Run Jobs.
- **Custom domain + Cloud Armor** — using `*.run.app` URLs.
- **Static egress IP** — Cloud NAT uses auto-allocated IPs.
- **Cost guardrails / Budget alerts** — test project, low blast radius.
- **dev/staging/prod split** — single stack against a single project.
- **KEDA Pub/Sub auth via TriggerAuthentication** — relies on KEDA operator's default SA having `pubsub.subscriber`. Add a `TriggerAuthentication` + Workload Identity binding when promoting to prod.
