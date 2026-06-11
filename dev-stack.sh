#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Local ForgeOS dev stack in a single tmux window (4 panes):
#
#   ┌──────────────────────┬──────────────────────┐
#   │ ForgeOS server       │ Redis logs           │
#   │ (run-local-server.sh)│ (docker logs -f)     │
#   ├──────────────────────┼──────────────────────┤
#   │ forgeos CLI          │ Postgres logs        │
#   │ (health + list)      │ (docker logs -f)     │
#   └──────────────────────┴──────────────────────┘
#
# Postgres + Redis run as Docker containers; ForgeOS runs locally on the host.
# Usage: ./dev-stack.sh   (run from inside tmux; opens a new window)
# ----------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"
# Root docker-compose.yaml; compose reads ./.env automatically when present.
COMPOSE=(docker compose)
SESSION="${TMUX_SESSION:-0}"
WINDOW="forgeos-stack"

# 1. Docker daemon -----------------------------------------------------------
if ! docker info >/dev/null 2>&1; then
  echo "Starting Docker Desktop…"
  open -a Docker
  tries=60
  until docker info >/dev/null 2>&1; do
    ((tries-- > 0)) || { echo "Docker failed to start"; exit 1; }
    sleep 2
  done
fi
echo "Docker is running."

# 2. Bring up postgres + redis (ForgeOS itself runs on the host) -------------
echo "Bringing up postgres + redis containers…"
"${COMPOSE[@]}" up -d postgres redis

wait_healthy() {
  local svc="$1" tries=60 cid
  while ((tries-- > 0)); do
    cid="$("${COMPOSE[@]}" ps -q "$svc" 2>/dev/null || true)"
    if [ -n "$cid" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)" = "healthy" ]; then
      echo "  $svc: healthy"; return 0
    fi
    sleep 2
  done
  echo "  WARN: $svc not healthy after timeout (continuing)"; return 0
}
echo "Waiting for health…"
wait_healthy postgres
wait_healthy redis

# 3. tmux window with 4 panes ------------------------------------------------
CLI_CMD='echo "waiting for ForgeOS on :5000…"; until forgeos health >/dev/null 2>&1; do sleep 2; done; forgeos health; echo; forgeos list; exec $SHELL'
REDIS_LOGS="${COMPOSE[*]} logs -f --tail=50 redis"
PG_LOGS="${COMPOSE[*]} logs -f --tail=50 postgres"
REDIS_CLI="${COMPOSE[*]} exec redis redis-cli"
PG_CLI="${COMPOSE[*]} exec postgres psql -U leadforge_admin -d leadforge"

WIN="${SESSION}:${WINDOW}"
tmux kill-window -t "$WIN" 2>/dev/null || true

# Capture pane IDs (%N) so layout is robust to pane-base-index settings.
P0=$(tmux new-window -P -F '#{pane_id}' -t "$SESSION" -n "$WINDOW" -c "$REPO")  # top-left: ForgeOS server
tmux send-keys -t "$P0" "./run-local-server.sh" C-m

P1=$(tmux split-window -P -F '#{pane_id}' -h -t "$P0" -c "$REPO")               # top-right: Redis logs
tmux send-keys -t "$P1" "$REDIS_LOGS" C-m

P2=$(tmux split-window -P -F '#{pane_id}' -v -t "$P1" -c "$REPO")               # bottom-right: Postgres logs
tmux send-keys -t "$P2" "$PG_LOGS" C-m

P3=$(tmux split-window -P -F '#{pane_id}' -v -t "$P0" -c "$REPO")               # bottom-left: forgeos CLI
tmux send-keys -t "$P3" "$CLI_CMD" C-m

P4=$(tmux split-window -P -F '#{pane_id}' -v -t "$P1" -c "$REPO")               # redis-cli (inspect Redis DB)
tmux send-keys -t "$P4" "$REDIS_CLI" C-m

P5=$(tmux split-window -P -F '#{pane_id}' -v -t "$P2" -c "$REPO")               # psql (inspect Postgres DB)
tmux send-keys -t "$P5" "$PG_CLI" C-m

tmux select-layout -t "$WIN" tiled
tmux select-window -t "$WIN"
echo "Done — switched to tmux window '${WINDOW}'."
