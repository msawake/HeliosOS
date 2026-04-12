# Deployment Guide

## Local Development

The simplest setup -- everything runs on your machine:

```bash
# Install
pip install -e ".[dev]"
cd dashboard && npm install && cd ..

# Configure
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env

# Boot backend
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000

# Boot dashboard (separate terminal)
cd dashboard && npm run dev
```

Data is in-memory. Add `DATABASE_URL` for persistence (see below).

---

## Docker Compose (Local with Persistence)

Run PostgreSQL and Redis via Docker for persistent storage:

```bash
# 1. Generate credentials
cd infrastructure/docker
bash docker-setup.sh

# 2. Start services
docker compose up -d postgres redis

# 3. Add DATABASE_URL to project .env
DB_PASS=$(grep DB_PASSWORD .env | cut -d= -f2)
echo "DATABASE_URL=postgresql://leadforge_admin:${DB_PASS}@localhost:5433/leadforge" >> ../../.env
echo "REDIS_URL=redis://localhost:6379" >> ../../.env

# 4. Boot the platform (migrations run automatically)
cd ../..
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
```

### Full Stack (API + Postgres + Redis)

To run the API server in Docker too:

```bash
cd infrastructure/docker
docker compose up --build
```

This starts all three services. The API listens on port 5000.

### Docker Compose Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres` | `pgvector/pgvector:pg16` | 5433 | Database with vector extensions |
| `redis` | `redis:7-alpine` | 6379 | Rate limiting, caching |
| `app` | Built from `Dockerfile` | 5000 | ForgeOS API |

---

## Kubernetes

Kubernetes manifests are in `deploy/k8s/` using Kustomize overlays.

### Structure

```
deploy/k8s/
  base/                    # Shared resources
    deployment-api.yaml    # API deployment (2 replicas)
    deployment-web.yaml    # Dashboard deployment
    service-api.yaml       # ClusterIP service
    service-web.yaml       # ClusterIP service
    configmap.yaml         # Environment configuration
    pvc.yaml               # Persistent volume for agent state
    ingress.yaml           # External access
    hpa-api.yaml           # Horizontal pod autoscaler (2-10 replicas)
    networkpolicy.yaml     # Network segmentation
    pdb-api.yaml           # Pod disruption budget
    pdb-web.yaml           # Pod disruption budget
  overlays/
    dev/                   # Development: 1 replica, debug logging
    staging/               # Staging: 2 replicas, production-like
    prod/                  # Production: 3 replicas, resource limits
  chaos/                   # Chaos Mesh experiments
    cpu-stress.yaml
    db-connection-kill.yaml
    network-delay.yaml
    pod-failure.yaml
```

### Deploy

```bash
# Development
kubectl apply -k deploy/k8s/overlays/dev/

# Staging
kubectl apply -k deploy/k8s/overlays/staging/

# Production
kubectl apply -k deploy/k8s/overlays/prod/
```

### Secrets

Create the required secret before deploying:

```bash
kubectl create secret generic forgeos-secrets \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --from-literal=DATABASE_URL=postgresql://... \
  --from-literal=REDIS_URL=redis://...
```

---

## GCP (Terraform)

Infrastructure as code for Google Cloud Platform is in `infrastructure/terraform/gcp/`.

### Resources Provisioned

| Resource | Service | Purpose |
|----------|---------|---------|
| Cloud SQL | PostgreSQL 16 | Primary database |
| Memorystore | Redis 7 | Rate limiting, caching |
| Cloud Run | 2 services | API + dashboard |
| VPC | Private network | Network isolation |
| Secret Manager | 5 secrets | API keys, DB credentials |
| Cloud Storage | 1 bucket | Backups, exports |
| Budget Alerts | 2 alerts | $100 and $500 thresholds |

### Deploy

```bash
cd infrastructure/terraform/gcp

# Initialize
terraform init

# Plan
terraform plan -var="project_id=my-gcp-project" -var="region=us-central1"

# Apply
terraform apply -var="project_id=my-gcp-project" -var="region=us-central1"
```

### CI/CD

The `infrastructure/docker/cloudbuild.yaml` defines a Cloud Build pipeline:
1. Run tests (`pytest`)
2. Build Docker image
3. Push to Artifact Registry
4. Deploy to Cloud Run

---

## Database Migrations

Migrations are managed by `src/core/migrations.py`. SQL files live in `infrastructure/database/`:

| File | Content |
|------|---------|
| `001_schema.sql` | Core schema: tenants, users, agents, sessions, events, approvals, audit_log |
| `002_platform_tables.sql` | Platform: agent_registry, scheduled_jobs, event_subscriptions |
| `003_ontology_tables.sql` | Knowledge graph: entities, relationships, signals |
| `004_client_mcp_configs.sql` | Client MCP server configurations |
| `005_audit_log.sql` | No-op (already in 001) |

Migrations run automatically on boot. Track status in `schema_migrations` table. Skip with `FORGEOS_SKIP_MIGRATIONS=1`.

---

## Backup & Restore

Scripts in `infrastructure/scripts/`:

```bash
# Backup
bash infrastructure/scripts/db-backup.sh

# Restore
bash infrastructure/scripts/db-restore.sh backup-2026-04-12.sql
```

See `docs/runbooks/db-recovery.md` for detailed recovery procedures.
