# Deploying Helios OS on Google Cloud

This guide deploys Helios OS (API + Dashboard + Mission Control) on Google Cloud Run with Cloud SQL for persistence and Secret Manager for credentials.

> **Prefer Infrastructure-as-Code?** The steps below are the manual `gcloud`
> path. For a repeatable, automated deployment (GKE Autopilot for agent
> workloads, Cloud SQL + Memorystore + Pub/Sub, identity, networking,
> observability, secrets), use the Pulumi stack at the top-level
> [`pulumi/`](https://github.com/makingscience-awake/forgeos/tree/main/pulumi)
> directory — see `pulumi/README.md`.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    Google Cloud Run                           │
│                                                               │
│  forgeos-api         FastAPI + Kernel + Runtime               │
│  forgeos-dashboard   Next.js dashboard                        │
│  forgeos-mc          Mission Control (fleet visibility)       │
│                                                               │
│  Cloud SQL (PostgreSQL 16)  →  agents, audit trail, sessions │
│  Secret Manager             →  API keys, DB password          │
│  Artifact Registry          →  Docker images                  │
│  Cloud Build                →  CI/CD pipelines                │
└──────────────────────────────────────────────────────────────┘
```

## Prerequisites

- Google Cloud account with billing enabled
- `gcloud` CLI installed (`brew install google-cloud-sdk` on macOS)
- Docker (for local builds) or Cloud Build (for remote builds)
- API key for at least one LLM provider:
  - Anthropic: https://console.anthropic.com
  - OpenAI: https://platform.openai.com
  - Google (Gemini): Vertex AI enabled in your project

---

## Step 1: Create GCP Project

```bash
# Set your project ID (choose your own)
export PROJECT_ID=my-forgeos-prod
export REGION=europe-west1

# Create and configure
gcloud projects create $PROJECT_ID --name="Helios OS Platform"
gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com
```

## Step 2: Create Artifact Registry

```bash
gcloud artifacts repositories create forgeos \
  --repository-format=docker \
  --location=$REGION \
  --description="Helios OS Docker images"
```

## Step 3: Create Cloud SQL Database

```bash
# Generate a strong password
export DB_PASSWORD=$(openssl rand -hex 16)

# Create PostgreSQL instance
gcloud sql instances create forgeos-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=$REGION \
  --storage-size=10GB \
  --storage-auto-increase \
  --backup-start-time=03:00 \
  --availability-type=zonal

# Create database and user
gcloud sql databases create forgeos --instance=forgeos-db
gcloud sql users create forgeos_admin \
  --instance=forgeos-db \
  --password=$DB_PASSWORD

echo "DATABASE_URL=postgresql://forgeos_admin:${DB_PASSWORD}@/forgeos?host=/cloudsql/${PROJECT_ID}:${REGION}:forgeos-db"
```

## Step 4: Store Secrets

```bash
# Database password
echo -n "$DB_PASSWORD" | gcloud secrets create forgeos-db-password --data-file=-

# LLM API keys (add the ones you have)
echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-
echo -n "sk-proj-..." | gcloud secrets create openai-api-key --data-file=-

# Mission Control operator password
echo -n "$(openssl rand -hex 16)" | gcloud secrets create forgeos-mc-password --data-file=-
```

## Step 5: Create Service Account

```bash
# Service account for Cloud Run
gcloud iam service-accounts create forgeos-runner \
  --display-name="Helios OS Cloud Run"

SA_EMAIL=forgeos-runner@${PROJECT_ID}.iam.gserviceaccount.com

# Grant permissions
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/cloudsql.client"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor"
```

---

## Step 6: Build Images

### Option A: Cloud Build (recommended)

```bash
# Build API image
gcloud builds submit \
  --project=$PROJECT_ID \
  --timeout=900s \
  --config=/dev/stdin . <<EOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'build'
      - '-f'
      - 'infrastructure/docker/Dockerfile'
      - '--target'
      - 'api'
      - '-t'
      - '${REGION}-docker.pkg.dev/${PROJECT_ID}/forgeos/forgeos-api:latest'
      - '.'
images:
  - '${REGION}-docker.pkg.dev/${PROJECT_ID}/forgeos/forgeos-api:latest'
EOF

# Build Dashboard image — the dashboard lives in its own repo, which ships a
# cloudbuild.yaml that builds from its root context (not -f dashboard/Dockerfile).
git clone git@github.com:antonibergas-hue/forgeos-dashboard.git ../forgeos-dashboard
gcloud builds submit ../forgeos-dashboard \
  --project=$PROJECT_ID \
  --timeout=900s \
  --config=../forgeos-dashboard/cloudbuild.yaml \
  --substitutions=_REGION=$REGION,_REQUIRE_AUTH=1
# Pushes ${REGION}-docker.pkg.dev/${PROJECT_ID}/forgeos/forgeos-dashboard:latest,
# which the Pulumi stack consumes and deploys to Cloud Run.

# Build Mission Control image
gcloud builds submit \
  --project=$PROJECT_ID \
  --timeout=900s \
  --config=/dev/stdin . <<EOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args:
      - 'build'
      - '-f'
      - 'infrastructure/docker/Dockerfile.mission-control'
      - '-t'
      - '${REGION}-docker.pkg.dev/${PROJECT_ID}/forgeos/forgeos-mc:latest'
      - '.'
images:
  - '${REGION}-docker.pkg.dev/${PROJECT_ID}/forgeos/forgeos-mc:latest'
EOF
```

### Option B: Local Build + Push

```bash
# Configure Docker for Artifact Registry
gcloud auth configure-docker ${REGION}-docker.pkg.dev

# Build locally
docker build -f infrastructure/docker/Dockerfile --target api \
  -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/forgeos/forgeos-api:latest .

docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/forgeos/forgeos-api:latest
```

---

## Step 7: Deploy to Cloud Run

### Backend API

```bash
gcloud run deploy forgeos-api \
  --image=${REGION}-docker.pkg.dev/${PROJECT_ID}/forgeos/forgeos-api:latest \
  --region=$REGION \
  --service-account=$SA_EMAIL \
  --allow-unauthenticated \
  --memory=1Gi --cpu=2 \
  --min-instances=0 --max-instances=5 \
  --timeout=300 \
  --add-cloudsql-instances=${PROJECT_ID}:${REGION}:forgeos-db \
  --set-env-vars="PYTHONPATH=/app,COMPANY_ID=default,FORGEOS_KERNEL_MODE=production,FORGEOS_ALLOW_DEV_LOGIN=0,FORGEOS_SKIP_MCP=1" \
  --set-secrets="DATABASE_URL=forgeos-db-password:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest"
```

> **Note**: The `DATABASE_URL` secret must contain the full connection string, not just the password. Create it as:
> ```bash
> echo -n "postgresql://forgeos_admin:${DB_PASSWORD}@/forgeos?host=/cloudsql/${PROJECT_ID}:${REGION}:forgeos-db" \
>   | gcloud secrets create forgeos-database-url --data-file=-
> ```
> Then use `--set-secrets="DATABASE_URL=forgeos-database-url:latest"`.

### Dashboard

```bash
# Get the API URL
API_URL=$(gcloud run services describe forgeos-api --region=$REGION --format='value(status.url)')

gcloud run deploy forgeos-dashboard \
  --image=${REGION}-docker.pkg.dev/${PROJECT_ID}/forgeos/forgeos-dashboard:latest \
  --region=$REGION \
  --allow-unauthenticated \
  --memory=512Mi --cpu=1 \
  --min-instances=0 --max-instances=3 \
  --set-env-vars="INTERNAL_API_URL=${API_URL},NODE_ENV=production,PORT=3000,HOSTNAME=0.0.0.0"
```

### Mission Control

```bash
gcloud run deploy forgeos-mc \
  --image=${REGION}-docker.pkg.dev/${PROJECT_ID}/forgeos/forgeos-mc:latest \
  --region=$REGION \
  --allow-unauthenticated \
  --memory=256Mi --cpu=1 \
  --min-instances=0 --max-instances=2 \
  --set-env-vars="FORGEOS_API_URL=${API_URL}" \
  --set-secrets="FORGEOS_MC_PASSWORD=forgeos-mc-password:latest"
```

---

## Step 8: Verify Deployment

```bash
# Get service URLs
API_URL=$(gcloud run services describe forgeos-api --region=$REGION --format='value(status.url)')
DASH_URL=$(gcloud run services describe forgeos-dashboard --region=$REGION --format='value(status.url)')
MC_URL=$(gcloud run services describe forgeos-mc --region=$REGION --format='value(status.url)')

echo "API:        $API_URL"
echo "Dashboard:  $DASH_URL"
echo "Mission Control: $MC_URL"

# Health check
curl -s $API_URL/api/health | python3 -m json.tool

# Deploy a test agent
curl -s -X POST $API_URL/api/platform/agents \
  -H "Content-Type: application/json" \
  -d '{"name":"hello-world","stack":"forgeos","execution_type":"reflex","system_prompt":"Say hello"}'

# List agents
curl -s $API_URL/api/platform/agents | python3 -m json.tool
```

---

## CI/CD with GitHub Actions

The repository includes `.github/workflows/deploy.yml` which auto-deploys on push to `main`.

### One-Time Setup

1. **Run the WIF setup script** (creates service account + Workload Identity Federation):

```bash
bash scripts/setup-gcp-wif.sh
```

This outputs two values to add as GitHub secrets.

2. **Configure GitHub repo settings:**

**Settings > Secrets and variables > Actions > Variables:**

| Variable | Value |
|----------|-------|
| `GCP_PROJECT_ID` | Your GCP project ID (e.g., `my-forgeos-prod`) |
| `GCP_PROJECT_NUMBER` | Your GCP project number (find in Cloud Console) |
| `GCP_REGION` | Your preferred region (e.g., `europe-west1`) |

**Settings > Secrets and variables > Actions > Secrets:**

| Secret | Value |
|--------|-------|
| `GCP_WIF_PROVIDER` | Output from `setup-gcp-wif.sh` |
| `GCP_DEPLOYER_SA` | Output from `setup-gcp-wif.sh` |

3. **Push to main** — GitHub Actions builds and deploys all 3 services.

---

## Kubernetes Alternative

For Kubernetes deployment, Helios OS ships with Kustomize manifests:

```
deploy/k8s/
  base/               # Core resources
    deployment-api.yaml
    deployment-web.yaml
    service-api.yaml
    service-web.yaml
    ingress.yaml
    configmap.yaml
    networkpolicy.yaml
    hpa-api.yaml
    pdb-api.yaml
  overlays/
    dev/              # 1 replica, no TLS
    staging/          # 2 replicas, staging TLS
    prod/             # 3 replicas, production TLS, PDB
```

### Deploy to GKE

```bash
# Create cluster
gcloud container clusters create-auto forgeos-cluster \
  --region=$REGION

# Apply
kubectl apply -k deploy/k8s/overlays/prod/

# Verify
kubectl get pods -n forgeos
kubectl get ingress -n forgeos
```

The K8s deployment includes:
- Non-root containers with read-only root filesystem
- Network policies (default-deny with explicit ingress/egress)
- Horizontal pod autoscaler (3-10 replicas in prod)
- Pod disruption budgets (minAvailable: 2)
- Prometheus ServiceMonitor for metrics

See `deploy/k8s/README.md` for full details.

---

## Environment Variables Reference

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@/dbname?host=/cloudsql/project:region:instance` |
| `PYTHONPATH` | Python module path | `/app` |

### LLM Providers (at least one required)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic Claude models |
| `OPENAI_API_KEY` | OpenAI GPT models |
| `GOOGLE_API_KEY` or `GEMINI_API_KEY` | Google Gemini models |
| `GCP_PROJECT_ID` + `GCP_REGION` | Vertex AI (uses ADC) |

### Platform Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `COMPANY_ID` | `default` | Tenant/company identifier |
| `FORGEOS_KERNEL_MODE` | (unset) | Set to `production` to require real kernel (not stubs) |
| `FORGEOS_ALLOW_DEV_LOGIN` | `0` | Set to `1` to enable dev login (not for production) |
| `FORGEOS_AUTH_DISABLED` | (unset) | Set to `1` to disable auth (development only) |
| `FORGEOS_SKIP_MCP` | (unset) | Set to `1` to skip MCP server connections |
| `FORGEOS_SEED_HITL` | `1` | Set to `0` to disable demo HITL approvals |
| `REDIS_URL` | (unset) | Redis for distributed rate limiting (optional) |

### Mission Control

| Variable | Description |
|----------|-------------|
| `FORGEOS_API_URL` | Backend API URL |
| `FORGEOS_MC_PASSWORD` | Operator login password |
| `FORGEOS_API_TOKEN` | Bearer token forwarded to API |

---

## Security Checklist

Before going to production:

- [ ] `FORGEOS_KERNEL_MODE=production` set (prevents kernel stubs)
- [ ] `FORGEOS_ALLOW_DEV_LOGIN=0` set (disables dev auth)
- [ ] `FORGEOS_AUTH_DISABLED` NOT set (auth enabled)
- [ ] Database password in Secret Manager (not env var)
- [ ] API keys in Secret Manager (not env var)
- [ ] Cloud SQL has backups enabled
- [ ] Cloud SQL does NOT have a public IP (use Cloud SQL Proxy)
- [ ] Cloud Run services use a dedicated service account (not default compute)
- [ ] `.env` file is NOT committed to git (check `.gitignore`)
- [ ] Pre-commit secret detection installed (`detect-secrets` or similar)

---

## Cost Estimate

For a small deployment (5-20 agents, light usage):

| Service | Monthly Cost |
|---------|-------------|
| Cloud Run (API) | $5-15 (scales to zero) |
| Cloud Run (Dashboard) | $2-5 |
| Cloud Run (Mission Control) | $1-3 |
| Cloud SQL (db-f1-micro) | $8-12 |
| Secret Manager | <$1 |
| Artifact Registry | <$1 |
| **Total** | **~$20-35/month** |

LLM costs are separate and depend on usage (Gemini Flash: ~$0.15/M tokens, Claude Sonnet: ~$3/M tokens).
