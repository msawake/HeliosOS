#!/usr/bin/env bash
# One-time setup: Workload Identity Federation so GitHub Actions can deploy to GCP
# without long-lived service-account keys.
#
# Run this ONCE on your laptop. You need owner/IAM-admin on the GCP project.
# After it finishes, copy the two values it prints into GitHub repo secrets:
#   GCP_WIF_PROVIDER
#   GCP_DEPLOYER_SA
#
# Usage:  bash scripts/setup-gcp-wif.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-YOUR_PROJECT_ID}"
POOL_ID="${POOL_ID:-github-actions}"
PROVIDER_ID="${PROVIDER_ID:-github}"
SA_NAME="${SA_NAME:-forgeos-deployer}"
GITHUB_REPO="${GITHUB_REPO:-makingscience-awake/forgeos}"

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

echo "→ enabling required APIs"
gcloud services enable \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  --project="$PROJECT_ID" >/dev/null

echo "→ creating deployer service account ($SA_EMAIL)"
gcloud iam service-accounts create "$SA_NAME" \
  --project="$PROJECT_ID" \
  --display-name="GitHub Actions deployer" >/dev/null 2>&1 || true

echo "→ granting deployer permissions"
for role in \
  roles/run.admin \
  roles/iam.serviceAccountUser \
  roles/artifactregistry.writer \
  roles/cloudbuild.builds.editor \
  roles/storage.objectAdmin \
  roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$role" \
    --condition=None >/dev/null
done

# The deployer must be allowed to "actAs" the runtime SA used by Cloud Run services.
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --project="$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountUser" >/dev/null

echo "→ creating Workload Identity Pool '$POOL_ID'"
gcloud iam workload-identity-pools create "$POOL_ID" \
  --project="$PROJECT_ID" \
  --location="global" \
  --display-name="GitHub Actions" >/dev/null 2>&1 || true

echo "→ creating Workload Identity Provider '$PROVIDER_ID'"
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --project="$PROJECT_ID" \
  --location="global" \
  --workload-identity-pool="$POOL_ID" \
  --display-name="GitHub OIDC" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='${GITHUB_REPO}'" \
  --issuer-uri="https://token.actions.githubusercontent.com" >/dev/null 2>&1 || true

echo "→ binding the GitHub repo to the deployer SA"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GITHUB_REPO}" >/dev/null

PROVIDER_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"

cat <<EOF

────────────────────────────────────────────────────────────────────
✓ Setup complete. Add these two secrets to GitHub:

  Repository → Settings → Secrets and variables → Actions → New secret

  Name:   GCP_WIF_PROVIDER
  Value:  ${PROVIDER_RESOURCE}

  Name:   GCP_DEPLOYER_SA
  Value:  ${SA_EMAIL}

Then push to main and the workflow at .github/workflows/deploy.yml will fire.
────────────────────────────────────────────────────────────────────
EOF
