#!/usr/bin/env bash
# Provision a PER-AGENT Google service account so a user can share a Drive
# folder with that agent's SA email and the agent reads/writes context files
# (keyless impersonation, drive.file scope — the SA only sees what's shared).
#
# Each agent gets its OWN SA: drive-agent-<slug>@<project>.iam.gserviceaccount.com
# This is the per-agent identity model (vs. one shared platform SA).
#
# Idempotent — safe to re-run.
#
# Usage:
#   ./scripts/provision_agent_sa.sh <slug> [project]
#   # or provision all treasury agents at once:
#   for s in bank-sap debt po mapping kyriba; do ./scripts/provision_agent_sa.sh "$s"; done
#
# What it does, for the given <slug>:
#   1. Enables the IAM Credentials API (idempotent).
#   2. Creates drive-agent-<slug> (if absent). NOTE: GCP SA ids are <=30 chars,
#      so use a SHORT slug (e.g. bank-sap, debt, po, mapping, kyriba).
#   3. Grants roles/iam.serviceAccountTokenCreator on it to BOTH:
#        - the human operator running locally (ADC impersonation source), and
#        - the platform runtime SA (prod Cloud Run).
#   4. Prints the SA email to put in the agent manifest (spec.drive.service_account)
#      and to share the Drive folder with.

set -euo pipefail

SLUG="${1:?usage: provision_agent_sa.sh <slug> [project]   (slug e.g. bank-sap)}"
PROJECT="${2:-${PROJECT:-admachina-atomic-test-84}}"
RUNTIME_SA="${RUNTIME_SA:-forgeos-api-sa@${PROJECT}.iam.gserviceaccount.com}"
# Local impersonation source: the gcloud user whose ADC the dev backend uses.
OPERATOR="${OPERATOR:-$(gcloud config get-value account 2>/dev/null)}"

SA_NAME="drive-agent-${SLUG}"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

if [[ ${#SA_NAME} -gt 30 ]]; then
  echo "✗ SA id '${SA_NAME}' exceeds GCP's 30-char limit — use a shorter slug." >&2
  exit 1
fi

echo "▶ project=${PROJECT} sa=${SA_EMAIL}"

echo "▶ enabling iamcredentials API"
gcloud services enable iamcredentials.googleapis.com --project="${PROJECT}" >/dev/null

if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "▶ creating SA ${SA_EMAIL}"
  gcloud iam service-accounts create "${SA_NAME}" \
    --project="${PROJECT}" \
    --display-name="ForgeOS Drive Agent — ${SLUG}" \
    --description="Per-agent Drive SA for treasury agent ${SLUG}. Authorized via Drive folder sharing."
else
  echo "▶ SA already exists"
fi

# Grant tokenCreator to the prod runtime SA (Cloud Run impersonates it there).
echo "▶ granting tokenCreator to runtime SA ${RUNTIME_SA}"
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project="${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/iam.serviceAccountTokenCreator" --quiet >/dev/null

# Grant tokenCreator to the local operator (dev backend impersonates via user ADC).
if [[ -n "${OPERATOR}" ]]; then
  echo "▶ granting tokenCreator to operator ${OPERATOR} (local ADC)"
  gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --project="${PROJECT}" \
    --member="user:${OPERATOR}" \
    --role="roles/iam.serviceAccountTokenCreator" --quiet >/dev/null
fi

cat <<EOF

✅ ${SA_EMAIL} ready.

Next:
  1. Put it in the agent manifest:
       spec:
         drive:
           service_account: ${SA_EMAIL}
           folder_id: <your-folder-id>
           access: readwrite
  2. Create a folder in a SHARED DRIVE (service accounts have no personal Drive
     quota — writing reports needs a Shared Drive), upload the agent's context
     files, and add ${SA_EMAIL} as **Content Manager**.
  3. Put the folder id into spec.drive.folder_id and redeploy.
EOF
