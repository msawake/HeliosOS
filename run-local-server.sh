#!/usr/bin/env bash
# Local dev launcher for the Helios OS platform with the per-turn worker tier +
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
#   ./run-local-server.sh                # reads config from ./.env (PORT, FLOWER, ...)
#   PORT=5055 FLOWER=1 ./run-local-server.sh   # inline override still works
#
# Recommended: put `PORT=5055` and `FLOWER=1` in ./.env, then just run
# `./run-local-server.sh`. Gmail send needs the FORGEOS_GWS_* vars (in .env or
# inline) or ADC.
set -euo pipefail
cd "$(dirname "$0")"

# --- Load .env --------------------------------------------------------------
# Put local config here instead of typing it inline. Recognized keys include
# PORT, FLOWER, BEAT, DATABASE_URL, REDIS_URL, VLLM_BASE_URL, VLLM_API_KEY,
# FORGEOS_* and the Gmail FORGEOS_GWS_* vars. A var already set in your shell
# wins, so you can still override per-run, e.g. `PORT=5050 ./run-local-server.sh`.
if [[ -f .env ]]; then
  while IFS= read -r _l || [[ -n "$_l" ]]; do
    case "$_l" in ''|\#*) continue ;; esac   # skip blank + comment lines
    _l="${_l#export }"                         # tolerate `export KEY=...`
    case "$_l" in *=*) ;; *) continue ;; esac  # require KEY=VALUE
    _k="${_l%%=*}"
    printenv "$_k" >/dev/null 2>&1 || export "$_k=${_l#*=}"  # only if unset
  done < .env
fi

PORT="${PORT:-5000}"

# One server per Redis: a stray second instance shares the Redis stream and
# steals resume tasks (non-deterministic failures). Kill any existing backend
# AND any worker tier we previously launched before starting fresh.
pkill -f "src.bootstrap --no-auth --dashboard --port ${PORT}" 2>/dev/null || true
pkill -f "celery -A forgeos_web.celery_app worker" 2>/dev/null || true
pkill -f "celery -A forgeos_web.celery_app beat" 2>/dev/null || true
[[ "${FLOWER:-0}" == "1" ]] && pkill -f "celery -A forgeos_web.celery_app flower" 2>/dev/null || true
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

echo "Booting Helios OS on :${PORT}  (Django backend + Celery worker tier + verbose kernel)"
echo "  Postgres: ${DATABASE_URL}"
echo "  Redis:    ${REDIS_URL}"
echo "  Qwen:     ${VLLM_BASE_URL:-<unset>}"
echo "  Gmail:    $([[ -n "${FORGEOS_GWS_CLIENT_ID:-}" ]] && echo 'FORGEOS_GWS_* set' || echo 'not set (sends will report missing creds)')"
echo

# --- Celery worker tier -----------------------------------------------------
# Post-migration, agents execute ONLY on the worker: the Django web process
# enqueues forgeos.run_agent and polls for the result. Without a worker, every
# chat/invoke enqueues and hangs forever — and nothing shows up in Flower. So
# the launcher always brings a worker up alongside the backend, mirroring the
# `worker` service in docker-compose.yaml. --concurrency=1 keeps it to a single
# platform boot (each worker process boots the platform in worker_process_init).
BG_PIDS=()
cleanup() {
  for p in "${BG_PIDS[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null || true; done
}
trap cleanup EXIT INT TERM

WORKER_LOG="$PWD/.local-worker.log"
echo "Starting Celery worker (queues: agents,agents_resume,scheduled,agents_longrun) -> ${WORKER_LOG}"
env PYTHONPATH=. .venv/bin/celery -A forgeos_web.celery_app worker \
  -Q agents,agents_resume,scheduled,agents_longrun \
  --concurrency=1 --loglevel=info >"${WORKER_LOG}" 2>&1 &
BG_PIDS+=("$!")

# Optional: Beat (fires SCHEDULED agents). Needs the django_celery_beat tables,
# so it is opt-in to avoid breaking boot where they are not migrated yet.
if [[ "${BEAT:-0}" == "1" ]]; then
  BEAT_LOG="$PWD/.local-beat.log"
  echo "Starting Celery beat -> ${BEAT_LOG}"
  env PYTHONPATH=. .venv/bin/celery -A forgeos_web.celery_app beat \
    --scheduler django_celery_beat.schedulers:DatabaseScheduler --loglevel=info \
    >"${BEAT_LOG}" 2>&1 &
  BG_PIDS+=("$!")
fi

# Optional: Flower task monitor on :5555 (the worker reports tasks here). Opt-in
# so it does not clash with an already-running Flower.
if [[ "${FLOWER:-0}" == "1" ]]; then
  FLOWER_LOG="$PWD/.local-flower.log"
  echo "Starting Flower on http://127.0.0.1:5555 -> ${FLOWER_LOG}"
  env PYTHONPATH=. .venv/bin/celery -A forgeos_web.celery_app flower --port=5555 \
    >"${FLOWER_LOG}" 2>&1 &
  BG_PIDS+=("$!")
fi

# The worker boots the platform on startup; give it a moment and fail fast with
# the log if it died on import/boot rather than leaving a silent enqueue-only box.
sleep 4
if ! kill -0 "${BG_PIDS[0]}" 2>/dev/null; then
  echo "ERROR: Celery worker exited during startup. Last 40 lines of ${WORKER_LOG}:" >&2
  tail -n 40 "${WORKER_LOG}" >&2 || true
  exit 1
fi
echo "Celery worker is up (pid ${BG_PIDS[0]}). Tail it with: tail -f ${WORKER_LOG}"
echo

# Backend in the FOREGROUND (not exec) so the EXIT trap reaps the worker tier on
# Ctrl-C. Boots the platform and serves the Django ASGI app (run_django_server).
env PYTHONPATH=. .venv/bin/python -m src.bootstrap --no-auth --dashboard --port "${PORT}"
