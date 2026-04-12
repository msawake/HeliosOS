#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Sequential chaos suite runner.
#
# Runs all chaos experiments one after another with a cooldown between them.
# Total duration: ~60 minutes.
#
# Usage:
#   bash deploy/k8s/chaos/run-all.sh
#
# CRITICAL: Only run this against a STAGING cluster, never production.
# ----------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")"

# Safety: require explicit confirmation
if [[ "${FORGEOS_CHAOS_CONFIRM:-}" != "yes-i-am-in-staging" ]]; then
  echo "ERROR: This script runs destructive chaos experiments."
  echo ""
  echo "Set FORGEOS_CHAOS_CONFIRM=yes-i-am-in-staging to proceed."
  echo ""
  echo "Current kubectl context:"
  kubectl config current-context || true
  exit 1
fi

# Safety: refuse to run if kubectl context looks like prod
CONTEXT=$(kubectl config current-context)
if [[ "$CONTEXT" == *prod* ]] || [[ "$CONTEXT" == *production* ]]; then
  echo "ERROR: kubectl context '$CONTEXT' looks like production. Aborting."
  exit 2
fi

EXPERIMENTS=(
  pod-failure.yaml
  network-delay.yaml
  cpu-stress.yaml
  db-connection-kill.yaml
)

for exp in "${EXPERIMENTS[@]}"; do
  echo "=== Applying $exp ==="
  kubectl apply -f "$exp"

  # Parse duration from manifest (simple yq-less approach)
  duration=$(grep -E "^\s*duration:" "$exp" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
  echo "  Duration: $duration"

  # Sleep through the experiment + 30s cooldown
  case "$duration" in
    *m) secs=$(( ${duration%m} * 60 )) ;;
    *s) secs=${duration%s} ;;
    *)  secs=300 ;;  # Fallback 5 min
  esac

  sleep $((secs + 30))

  echo "=== Cleaning up $exp ==="
  kubectl delete -f "$exp" --ignore-not-found
  echo ""
done

echo "All chaos experiments completed."
