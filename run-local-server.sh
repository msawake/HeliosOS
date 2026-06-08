#!/usr/bin/env bash
# Local dev launcher for the ForgeOS platform with the per-turn worker tier +
# verbose kernel logging. Runs in the FOREGROUND so you watch kernel decisions
# live; run `forgeos chat <agent>` from another terminal. Ctrl-C to stop.
#
# Secrets are NOT hardcoded:
#   - Qwen (vLLM) key/url are read from ~/.config/opencode/config.json at runtime.
#   - Gmail send needs FORGEOS_GWS_* — export them before running (or rely on
#     Secret Manager via ADC). Without them, notify__email stops at
#     "Gmail credentials missing" (the rest of the approval flow still works).
#
# Usage:
#   ./run-local-server.sh                # boot on :5000
#   FORGEOS_GWS_CLIENT_ID=... FORGEOS_GWS_CLIENT_SECRET=... \
#     FORGEOS_GWS_REFRESH_TOKEN=... ./run-local-server.sh   # real email send
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-5000}"

# One server per Redis: a stray second instance shares the Redis stream and
# steals resume tasks (non-deterministic failures). Kill any existing one first.
pkill -f "src.bootstrap --no-auth --dashboard --port ${PORT}" 2>/dev/null || true
sleep 1

# Qwen gateway from opencode config (override by exporting VLLM_* yourself).
OPENCODE="$HOME/.config/opencode/config.json"
if [[ -z "${VLLM_BASE_URL:-}" || -z "${VLLM_API_KEY:-}" ]] && [[ -f "$OPENCODE" ]]; then
  export VLLM_BASE_URL="${VLLM_BASE_URL:-$(.venv/bin/python -c "import json,os;print(json.load(open(os.path.expanduser('$OPENCODE')))['provider']['atlas']['options']['baseURL'])")}"
  export VLLM_API_KEY="${VLLM_API_KEY:-$(.venv/bin/python -c "import json,os;print(json.load(open(os.path.expanduser('$OPENCODE')))['provider']['atlas']['options']['apiKey'])")}"
fi

export DATABASE_URL="${DATABASE_URL:-postgresql://leadforge_admin:forgeoslocal@localhost:5433/leadforge}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379}"
export FORGEOS_RUNTIME_V2="${FORGEOS_RUNTIME_V2:-1}"          # durable continuation engine
export FORGEOS_RUNTIME_WORKERS="${FORGEOS_RUNTIME_WORKERS:-1}" # worker tier (per-turn via Redis)
export FORGEOS_SYSCALL_PIPELINE="${FORGEOS_SYSCALL_PIPELINE:-1}" # unified kernel admission (honors approval tokens)
export FORGEOS_KERNEL_VERBOSE="${FORGEOS_KERNEL_VERBOSE:-1}"   # narrate every kernel decision

# Gmail send: pull FORGEOS_GWS_* from Secret Manager in this project via ADC
# (gcloud application-default creds). Override/unset to disable real sends.
export GCP_PROJECT_ID="${GCP_PROJECT_ID:-admachina-atomic-test-84}"

echo "Booting ForgeOS on :${PORT}  (worker tier + verbose kernel)"
echo "  Postgres: ${DATABASE_URL}"
echo "  Redis:    ${REDIS_URL}"
echo "  Qwen:     ${VLLM_BASE_URL:-<unset>}"
echo "  Gmail:    $([[ -n "${FORGEOS_GWS_CLIENT_ID:-}" ]] && echo 'FORGEOS_GWS_* set' || echo 'not set (sends will report missing creds)')"
echo

exec env PYTHONPATH=. .venv/bin/python -m src.bootstrap --no-auth --dashboard --port "${PORT}"
