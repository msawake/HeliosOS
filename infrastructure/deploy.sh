#!/usr/bin/env bash
# =============================================================================
# ForgeOS Deployment Script
# =============================================================================
#
# Deploys ForgeOS (API + Dashboard) to Google Cloud Run with:
#   - Cloud SQL (PostgreSQL 16)
#   - Secret Manager (all credentials)
#   - Artifact Registry (Docker images)
#   - IAP authentication (Google login for whitelisted users)
#
# Usage:
#   # Interactive (prompts for everything)
#   bash infrastructure/deploy.sh
#
#   # Non-interactive (provide all values)
#   bash infrastructure/deploy.sh \
#     --project=my-gcp-project \
#     --region=europe-west1 \
#     --anthropic-key=sk-ant-... \
#     --openai-key=sk-proj-... \
#     --users=user1@company.com,user2@company.com
#
# Prerequisites:
#   - gcloud CLI authenticated (gcloud auth login)
#   - Docker running (for building images)
#   - Project owner or editor role
#
# =============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${GREEN}[ForgeOS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
step() { echo -e "\n${CYAN}━━━ Step $1: $2 ━━━${NC}"; }

# =============================================================================
# Parse arguments
# =============================================================================

PROJECT_ID=""
REGION="europe-west1"
INSTANCE_NAME="forgeos"
ANTHROPIC_KEY=""
OPENAI_KEY=""
GWS_CLIENT_ID=""
GWS_CLIENT_SECRET=""
GWS_REFRESH_TOKEN=""
USERS=""
COMPANY_ID="leadforge"
SKIP_DB="false"
SKIP_DASHBOARD="false"

for arg in "$@"; do
  case $arg in
    --project=*)       PROJECT_ID="${arg#*=}" ;;
    --region=*)        REGION="${arg#*=}" ;;
    --instance=*)      INSTANCE_NAME="${arg#*=}" ;;
    --anthropic-key=*) ANTHROPIC_KEY="${arg#*=}" ;;
    --openai-key=*)    OPENAI_KEY="${arg#*=}" ;;
    --gws-client-id=*) GWS_CLIENT_ID="${arg#*=}" ;;
    --gws-client-secret=*) GWS_CLIENT_SECRET="${arg#*=}" ;;
    --gws-refresh-token=*) GWS_REFRESH_TOKEN="${arg#*=}" ;;
    --users=*)         USERS="${arg#*=}" ;;
    --company=*)       COMPANY_ID="${arg#*=}" ;;
    --skip-db)         SKIP_DB="true" ;;
    --skip-dashboard)  SKIP_DASHBOARD="true" ;;
    --help)
      echo "Usage: bash infrastructure/deploy.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --project=ID          GCP project ID (required)"
      echo "  --region=REGION       GCP region (default: europe-west1)"
      echo "  --instance=NAME       Instance name prefix (default: forgeos)"
      echo "  --anthropic-key=KEY   Anthropic API key"
      echo "  --openai-key=KEY      OpenAI API key"
      echo "  --gws-client-id=ID    Google Workspace OAuth client ID"
      echo "  --gws-client-secret=S Google Workspace OAuth client secret"
      echo "  --gws-refresh-token=T Google Workspace OAuth refresh token"
      echo "  --users=EMAILS        Comma-separated email addresses for IAP access"
      echo "  --company=ID          Company config to load (default: leadforge)"
      echo "  --skip-db             Skip database creation (reuse existing)"
      echo "  --skip-dashboard      Skip dashboard deployment (API only)"
      echo "  --help                Show this help"
      exit 0
      ;;
  esac
done

# =============================================================================
# Interactive prompts for missing values
# =============================================================================

if [ -z "$PROJECT_ID" ]; then
  CURRENT=$(gcloud config get-value project 2>/dev/null)
  read -p "GCP Project ID [$CURRENT]: " PROJECT_ID
  PROJECT_ID="${PROJECT_ID:-$CURRENT}"
fi

if [ -z "$PROJECT_ID" ]; then
  err "Project ID is required. Use --project=ID or gcloud config set project ID"
fi

gcloud config set project "$PROJECT_ID" 2>/dev/null

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null)
log "Project: $PROJECT_ID ($PROJECT_NUMBER)"
log "Region:  $REGION"
log "Instance: $INSTANCE_NAME"

if [ -z "$ANTHROPIC_KEY" ]; then
  # Try reading from local .env
  if [ -f .env ]; then
    ANTHROPIC_KEY=$(grep "^ANTHROPIC_API_KEY=" .env 2>/dev/null | cut -d= -f2 || true)
  fi
  if [ -z "$ANTHROPIC_KEY" ]; then
    read -sp "Anthropic API key: " ANTHROPIC_KEY
    echo ""
  else
    log "Anthropic key loaded from .env"
  fi
fi

if [ -z "$OPENAI_KEY" ]; then
  if [ -f .env ]; then
    OPENAI_KEY=$(grep "^OPENAI_API_KEY=" .env 2>/dev/null | cut -d= -f2 || true)
  fi
  if [ -z "$OPENAI_KEY" ]; then
    read -sp "OpenAI API key (or press Enter to skip): " OPENAI_KEY
    echo ""
  else
    log "OpenAI key loaded from .env"
  fi
fi

if [ -z "$GWS_CLIENT_ID" ] && [ -f .env ]; then
  GWS_CLIENT_ID=$(grep "^GOOGLE_WORKSPACE_CLIENT_ID=" .env 2>/dev/null | cut -d= -f2 || true)
  GWS_CLIENT_SECRET=$(grep "^GOOGLE_WORKSPACE_CLIENT_SECRET=" .env 2>/dev/null | cut -d= -f2 || true)
  GWS_REFRESH_TOKEN=$(grep "^GOOGLE_WORKSPACE_REFRESH_TOKEN=" .env 2>/dev/null | cut -d= -f2 || true)
  [ -n "$GWS_CLIENT_ID" ] && log "Google Workspace credentials loaded from .env"
fi

if [ -z "$USERS" ]; then
  read -p "IAP user emails (comma-separated): " USERS
fi

# =============================================================================
# Derived names
# =============================================================================

API_SERVICE="${INSTANCE_NAME}-api"
WEB_SERVICE="${INSTANCE_NAME}-web"
DB_INSTANCE="${INSTANCE_NAME}-db"
REPO_NAME="${INSTANCE_NAME}"
API_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${API_SERVICE}:latest"
WEB_IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${WEB_SERVICE}:latest"
DB_PASSWORD=$(openssl rand -hex 16)
SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
SECRET_PREFIX=$(echo "$INSTANCE_NAME" | tr '[:lower:]' '[:upper:]' | tr '-' '_')

log "API Service:  $API_SERVICE"
log "Web Service:  $WEB_SERVICE"
log "DB Instance:  $DB_INSTANCE"
log "Image Repo:   $REPO_NAME"

echo ""
read -p "Continue with deployment? (y/N) " CONFIRM
[ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ] || exit 0

# =============================================================================
# Step 1: Enable APIs
# =============================================================================

step 1 "Enabling GCP APIs"

gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  iap.googleapis.com \
  2>&1 | grep -v "already enabled" || true

log "APIs enabled"

# =============================================================================
# Step 2: Create Artifact Registry
# =============================================================================

step 2 "Creating Artifact Registry"

gcloud artifacts repositories create "$REPO_NAME" \
  --repository-format=docker \
  --location="$REGION" \
  --description="ForgeOS platform images" \
  2>&1 || warn "Repository already exists"

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet 2>/dev/null

log "Registry ready: ${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}"

# =============================================================================
# Step 3: Build and push Docker images
# =============================================================================

step 3 "Building and pushing Docker images"

log "Building API image (linux/amd64)..."
docker buildx build \
  --platform linux/amd64 \
  -f infrastructure/docker/Dockerfile \
  --target api \
  -t "$API_IMAGE" \
  --push \
  . 2>&1 | tail -3

log "API image pushed: $API_IMAGE"

if [ "$SKIP_DASHBOARD" != "true" ]; then
  # Ensure public dir exists for Next.js
  mkdir -p dashboard/public

  log "Building dashboard image (linux/amd64)..."
  docker buildx build \
    --platform linux/amd64 \
    -f dashboard/Dockerfile \
    -t "$WEB_IMAGE" \
    --push \
    . 2>&1 | tail -3

  log "Dashboard image pushed: $WEB_IMAGE"
fi

# =============================================================================
# Step 4: Create Cloud SQL database
# =============================================================================

if [ "$SKIP_DB" != "true" ]; then
  step 4 "Creating Cloud SQL instance"

  gcloud sql instances create "$DB_INSTANCE" \
    --database-version=POSTGRES_16 \
    --edition=ENTERPRISE \
    --tier=db-f1-micro \
    --region="$REGION" \
    --root-password="$DB_PASSWORD" \
    --storage-type=SSD \
    --storage-size=10 \
    --async \
    2>&1 || warn "Instance may already exist"

  log "Waiting for Cloud SQL to be ready..."
  for i in $(seq 1 60); do
    STATE=$(gcloud sql instances describe "$DB_INSTANCE" --format="value(state)" 2>/dev/null || echo "PENDING")
    [ "$STATE" = "RUNNABLE" ] && break
    printf "."
    sleep 5
  done
  echo ""

  gcloud sql databases create forgeos --instance="$DB_INSTANCE" 2>&1 || warn "Database already exists"
  gcloud sql users create forgeos_admin --instance="$DB_INSTANCE" --password="$DB_PASSWORD" 2>&1 || warn "User already exists"

  log "Cloud SQL ready: $DB_INSTANCE"
else
  log "Skipping database creation (--skip-db)"
  read -sp "Existing DB password: " DB_PASSWORD
  echo ""
fi

CLOUD_SQL_CONN="${PROJECT_ID}:${REGION}:${DB_INSTANCE}"
DATABASE_URL="postgresql://forgeos_admin:${DB_PASSWORD}@/forgeos?host=/cloudsql/${CLOUD_SQL_CONN}"

# =============================================================================
# Step 5: Store secrets
# =============================================================================

step 5 "Storing secrets in Secret Manager"

store_secret() {
  local name="$1"
  local value="$2"
  if [ -z "$value" ]; then
    warn "Skipping empty secret: $name"
    return
  fi
  echo -n "$value" | gcloud secrets create "$name" --data-file=- 2>&1 || \
  echo -n "$value" | gcloud secrets versions add "$name" --data-file=- 2>&1
  log "  Secret: $name"
}

store_secret "${SECRET_PREFIX}_ANTHROPIC_KEY" "$ANTHROPIC_KEY"
store_secret "${SECRET_PREFIX}_OPENAI_KEY" "$OPENAI_KEY"
store_secret "${SECRET_PREFIX}_DATABASE_URL" "$DATABASE_URL"
store_secret "${SECRET_PREFIX}_DB_PASSWORD" "$DB_PASSWORD"

if [ -n "$GWS_CLIENT_ID" ]; then
  store_secret "${SECRET_PREFIX}_GWS_CLIENT_ID" "$GWS_CLIENT_ID"
  store_secret "${SECRET_PREFIX}_GWS_CLIENT_SECRET" "$GWS_CLIENT_SECRET"
  store_secret "${SECRET_PREFIX}_GWS_REFRESH_TOKEN" "$GWS_REFRESH_TOKEN"
fi

# Grant Cloud Run service account access to secrets
gcloud secrets add-iam-policy-binding "${SECRET_PREFIX}_ANTHROPIC_KEY" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet 2>/dev/null || true

log "Secrets stored"

# =============================================================================
# Step 6: Deploy API to Cloud Run
# =============================================================================

step 6 "Deploying ForgeOS API"

SECRETS_FLAG="ANTHROPIC_API_KEY=${SECRET_PREFIX}_ANTHROPIC_KEY:latest"
SECRETS_FLAG="$SECRETS_FLAG,DATABASE_URL=${SECRET_PREFIX}_DATABASE_URL:latest"
[ -n "$OPENAI_KEY" ] && SECRETS_FLAG="$SECRETS_FLAG,OPENAI_API_KEY=${SECRET_PREFIX}_OPENAI_KEY:latest"
[ -n "$GWS_CLIENT_ID" ] && SECRETS_FLAG="$SECRETS_FLAG,GOOGLE_WORKSPACE_CLIENT_ID=${SECRET_PREFIX}_GWS_CLIENT_ID:latest,GOOGLE_WORKSPACE_CLIENT_SECRET=${SECRET_PREFIX}_GWS_CLIENT_SECRET:latest,GOOGLE_WORKSPACE_REFRESH_TOKEN=${SECRET_PREFIX}_GWS_REFRESH_TOKEN:latest"

gcloud run deploy "$API_SERVICE" \
  --image="$API_IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --port=5000 \
  --cpu=2 \
  --memory=2Gi \
  --min-instances=1 \
  --max-instances=5 \
  --timeout=300 \
  --no-allow-unauthenticated \
  --set-env-vars="PYTHONPATH=/app,COMPANY_ID=${COMPANY_ID},FORGEOS_SEED_HITL=0,FORGEOS_MCP_BOOT_TIMEOUT=45,FORGEOS_AUTH_DISABLED=1" \
  --set-secrets="$SECRETS_FLAG" \
  --add-cloudsql-instances="$CLOUD_SQL_CONN" \
  --service-account="$SERVICE_ACCOUNT" \
  2>&1 | tail -3

API_URL=$(gcloud run services describe "$API_SERVICE" --region="$REGION" --format="value(status.url)" 2>/dev/null)
log "API deployed: $API_URL"

# =============================================================================
# Step 7: Deploy Dashboard to Cloud Run
# =============================================================================

if [ "$SKIP_DASHBOARD" != "true" ]; then
  step 7 "Deploying ForgeOS Dashboard"

  gcloud run deploy "$WEB_SERVICE" \
    --image="$WEB_IMAGE" \
    --region="$REGION" \
    --platform=managed \
    --port=3000 \
    --cpu=1 \
    --memory=512Mi \
    --min-instances=0 \
    --max-instances=3 \
    --no-allow-unauthenticated \
    --set-env-vars="NODE_ENV=production,SKIP_API_REWRITE=1,INTERNAL_API_URL=${API_URL},NEXT_PUBLIC_API_URL=${API_URL}" \
    2>&1 | tail -3

  WEB_URL=$(gcloud run services describe "$WEB_SERVICE" --region="$REGION" --format="value(status.url)" 2>/dev/null)
  log "Dashboard deployed: $WEB_URL"
fi

# =============================================================================
# Step 8: Configure IAP access
# =============================================================================

step 8 "Configuring IAP access"

if [ -n "$USERS" ]; then
  IFS=',' read -ra USER_LIST <<< "$USERS"
  for USER in "${USER_LIST[@]}"; do
    USER=$(echo "$USER" | xargs)  # trim whitespace
    log "  Adding: $USER"

    gcloud run services add-iam-policy-binding "$API_SERVICE" \
      --region="$REGION" \
      --member="user:$USER" \
      --role="roles/run.invoker" \
      --quiet 2>/dev/null

    if [ "$SKIP_DASHBOARD" != "true" ]; then
      gcloud run services add-iam-policy-binding "$WEB_SERVICE" \
        --region="$REGION" \
        --member="user:$USER" \
        --role="roles/run.invoker" \
        --quiet 2>/dev/null
    fi
  done
  log "${#USER_LIST[@]} users configured"
fi

# =============================================================================
# Step 9: Verify
# =============================================================================

step 9 "Verifying deployment"

echo ""
log "Testing API health..."
TOKEN=$(gcloud auth print-identity-token --audiences="$API_URL" 2>/dev/null || true)
if [ -n "$TOKEN" ]; then
  HEALTH=$(curl -s -H "Authorization: Bearer $TOKEN" "$API_URL/api/health" 2>/dev/null || echo '{"status":"unreachable"}')
  echo "  $HEALTH" | python3 -m json.tool 2>/dev/null || echo "  $HEALTH"
fi

# =============================================================================
# Summary
# =============================================================================

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ForgeOS Deployment Complete${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${CYAN}API:${NC}        $API_URL"
[ -n "${WEB_URL:-}" ] && echo -e "  ${CYAN}Dashboard:${NC}  $WEB_URL"
echo -e "  ${CYAN}Swagger:${NC}    $API_URL/docs"
echo -e "  ${CYAN}Admin:${NC}      $API_URL/admin"
echo ""
echo -e "  ${CYAN}Project:${NC}    $PROJECT_ID"
echo -e "  ${CYAN}Region:${NC}     $REGION"
echo -e "  ${CYAN}Database:${NC}   $DB_INSTANCE"
echo -e "  ${CYAN}Company:${NC}    $COMPANY_ID"
echo ""
echo -e "  ${CYAN}Users:${NC}      ${USERS:-none}"
echo ""
echo -e "  ${YELLOW}Enable IAP in Cloud Run console:${NC}"
echo -e "  1. Open: https://console.cloud.google.com/run?project=$PROJECT_ID"
echo -e "  2. Click $API_SERVICE → Security → Identity-Aware Proxy (IAP)"
echo -e "  3. Click $WEB_SERVICE → Security → Identity-Aware Proxy (IAP)"
echo ""
echo -e "  ${YELLOW}To add more users later:${NC}"
echo -e "  gcloud run services add-iam-policy-binding $API_SERVICE \\"
echo -e "    --region=$REGION --member=user:NEW@EMAIL --role=roles/run.invoker"
echo ""
echo -e "  ${YELLOW}To redeploy after code changes:${NC}"
echo -e "  bash infrastructure/deploy.sh --project=$PROJECT_ID --region=$REGION --skip-db"
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
