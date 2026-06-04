#!/usr/bin/env bash
# Stand up the Marbury & Stone law firm LOCALLY: kind cluster + agent-base image
# + KEDA + the Pulumi `local` stack (4 per-agent pods, KEDA scale-to-zero).
# Idempotent — safe to re-run. Needs: docker, kind, kubectl, helm, pulumi,
# and GEMINI_API_KEY (read from .env). Zero GCP credentials required.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLUSTER="forgeos"
CTX="kind-forgeos"
IMAGE="forgeos/agent-base:dev"
export PULUMI_CONFIG_PASSPHRASE="${PULUMI_CONFIG_PASSPHRASE:-forgeos-local}"
GEMINI_API_KEY="$(grep '^GEMINI_API_KEY=' "$REPO/.env" | cut -d= -f2- | tr -d '"')"
export GEMINI_API_KEY

echo "==> 1/5 kind cluster"
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER"
else
  echo "    cluster '$CLUSTER' exists"
fi

echo "==> 2/5 build + load agent-base image"
docker build -q -f "$REPO/infrastructure/docker/Dockerfile.agent-base" -t "$IMAGE" "$REPO" >/dev/null
kind load docker-image "$IMAGE" --name "$CLUSTER"

echo "==> 3/5 KEDA"
if ! helm status keda -n keda --kube-context "$CTX" >/dev/null 2>&1; then
  helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
  helm repo update >/dev/null
  helm install keda kedacore/keda -n keda --create-namespace --kube-context "$CTX" --wait --timeout 180s
else
  echo "    KEDA already installed"
fi

echo "==> 4/5 pulumi up (local stack)"
cd "$REPO/pulumi"
pulumi stack select local 2>/dev/null || pulumi stack init local --secrets-provider passphrase
pulumi up -s local --yes

echo "==> 5/5 done"
echo
"$REPO/scripts/local/lawfirm-check.sh" || true
