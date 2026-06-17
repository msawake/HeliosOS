#!/usr/bin/env bash
# Setup the drive-chat-agent's identity: a dedicated GCP service account that
# the Cloud Run runtime SA can impersonate (keyless). The user then shares
# Drive files/folders with that SA's email to authorize the agent.
#
# Idempotent — safe to re-run.
#
# Usage:
#   PROJECT=admachina-atomic-test-84 ./scripts/setup_drive_agent.sh
#
# What it does:
#   1. Enables the IAM Credentials and Drive APIs.
#   2. Creates the forgeos-drive-agent service account (if absent).
#   3. Grants the Cloud Run runtime SA roles/iam.serviceAccountTokenCreator
#      on the drive-agent SA so the platform can impersonate without a key.
#   4. Sets FORGEOS_DRIVE_AGENT_SA on the deployed Cloud Run service.
#
# After this:
#   - Share a Drive folder with the SA's email (printed at the end).
#   - Deploy the agent: forgeos deploy examples/drive-chat-agent/manifest.yaml
#   - Chat with it:     forgeos chat <agent-id>

set -euo pipefail

PROJECT="${PROJECT:-admachina-atomic-test-84}"
REGION="${REGION:-europe-west1}"
RUN_SERVICE="${RUN_SERVICE:-forgeos-platform-api}"
DRIVE_SA_NAME="${DRIVE_SA_NAME:-forgeos-drive-agent}"
DRIVE_SA_EMAIL="${DRIVE_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

echo "▶ project=${PROJECT} region=${REGION} drive_sa=${DRIVE_SA_EMAIL}"

# 1. Enable required APIs.
echo "▶ enabling APIs (iamcredentials, drive)"
gcloud services enable \
  iamcredentials.googleapis.com \
  drive.googleapis.com \
  --project="${PROJECT}"

# 2. Create the drive-agent SA (idempotent).
if ! gcloud iam service-accounts describe "${DRIVE_SA_EMAIL}" --project="${PROJECT}" >/dev/null 2>&1; then
  echo "▶ creating SA ${DRIVE_SA_EMAIL}"
  gcloud iam service-accounts create "${DRIVE_SA_NAME}" \
    --project="${PROJECT}" \
    --display-name="Helios OS Drive agent" \
    --description="Identity used by drive-chat-agent. Authorized via Drive sharing."
else
  echo "▶ SA already exists"
fi

# 3. Find the Cloud Run service's runtime SA so we can grant impersonation.
RUNTIME_SA=$(gcloud run services describe "${RUN_SERVICE}" \
  --project="${PROJECT}" --region="${REGION}" \
  --format='value(spec.template.spec.serviceAccountName)' 2>/dev/null || true)
if [[ -z "${RUNTIME_SA}" ]]; then
  # Fall back to the project's default compute SA (Cloud Run's default).
  PROJECT_NUMBER=$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')
  RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
  echo "▶ Cloud Run service has no explicit SA — assuming default compute SA: ${RUNTIME_SA}"
else
  echo "▶ Cloud Run runtime SA: ${RUNTIME_SA}"
fi

# 4. Grant token-creator on the drive-agent SA to the runtime SA.
echo "▶ granting roles/iam.serviceAccountTokenCreator to runtime SA on ${DRIVE_SA_EMAIL}"
gcloud iam service-accounts add-iam-policy-binding "${DRIVE_SA_EMAIL}" \
  --project="${PROJECT}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --quiet >/dev/null

# 5. Set FORGEOS_DRIVE_AGENT_SA on the live Cloud Run service so drive_tool.py
#    picks the right target. Skip if running locally / no Cloud Run.
if gcloud run services describe "${RUN_SERVICE}" --project="${PROJECT}" --region="${REGION}" >/dev/null 2>&1; then
  echo "▶ setting FORGEOS_DRIVE_AGENT_SA env on Cloud Run service ${RUN_SERVICE}"
  gcloud run services update "${RUN_SERVICE}" \
    --project="${PROJECT}" --region="${REGION}" \
    --update-env-vars="FORGEOS_DRIVE_AGENT_SA=${DRIVE_SA_EMAIL}" \
    --quiet >/dev/null
else
  echo "▶ Cloud Run service ${RUN_SERVICE} not found in ${REGION} — set FORGEOS_DRIVE_AGENT_SA manually wherever the platform runs"
fi

cat <<EOF

✅ drive-agent identity ready.

Next steps (manual, ~30 s each):

  1. Create a Drive folder (e.g. "Helios OS Drive Demo") in Google Drive.
  2. Right-click the folder → Share → add this email as **Editor**:
        ${DRIVE_SA_EMAIL}
     (Important: the agent can ONLY see files shared with that SA email.)

  3. Deploy the agent:
        forgeos deploy examples/drive-chat-agent/manifest.yaml

  4. Find its id:
        forgeos list

  5. Chat with it (create + read + modify files via conversation):
        forgeos chat <agent-id>
     Example prompts:
        list my files
        create a new file called tasks.md with "[ ] Buy milk"
        add a line "[ ] Call Alice" to tasks.md
        what's in notes.md?

EOF
