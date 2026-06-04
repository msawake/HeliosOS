#!/usr/bin/env bash
# Inspect the local law firm: show per-agent pods + autoscalers, then optionally
# wake an agent from zero and call it.
#   scripts/local/lawfirm-check.sh             # show state
#   scripts/local/lawfirm-check.sh wake        # LPUSH a backlog item to every agent -> KEDA scales them up
#   scripts/local/lawfirm-check.sh invoke law-firm-associate "draft a 1-line NDA summary"
set -euo pipefail
CTX="kind-forgeos"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AGENTS=(law-firm-associate conflicts-clerk risk-compliance-auditor docketing-clerk)

redis_pod() { kubectl --context "$CTX" -n forgeos-data get pod -l app=forgeos-redis -o jsonpath='{.items[0].metadata.name}'; }
ns_of() { case "$1" in conflicts-clerk) echo conflicts;; *) echo legal;; esac; }

case "${1:-show}" in
  show)
    echo "=== per-agent pods ==="
    kubectl --context "$CTX" get pods -A -l forgeos.io/agent -o wide 2>/dev/null || echo "  (none running — idle; run: $0 wake)"
    echo; echo "=== deployments (desired replicas; 0 = scaled to zero) ==="
    kubectl --context "$CTX" get deploy -A -l forgeos.io/agent -o custom-columns='NS:.metadata.namespace,AGENT:.metadata.name,DESIRED:.spec.replicas' --no-headers
    echo; echo "=== KEDA autoscalers ==="
    kubectl --context "$CTX" get scaledobject -A 2>/dev/null
    ;;
  wake)
    RP="$(redis_pod)"
    for a in "${AGENTS[@]}"; do
      kubectl --context "$CTX" -n forgeos-data exec "$RP" -- redis-cli LPUSH "agent:$a" wake >/dev/null && echo "queued: $a"
    done
    echo "KEDA will scale each 0 -> 1 in a few seconds. Watch:  kubectl --context $CTX get pods -A -l forgeos.io/agent -w"
    ;;
  sleep)
    RP="$(redis_pod)"
    for a in "${AGENTS[@]}"; do kubectl --context "$CTX" -n forgeos-data exec "$RP" -- redis-cli DEL "agent:$a" >/dev/null; done
    echo "drained all queues; KEDA returns agents to 0 after cooldown (~15s)."
    ;;
  invoke)
    AGENT="${2:?usage: $0 invoke <agent> <prompt>}"; PROMPT="${3:?usage: $0 invoke <agent> <prompt>}"; NS="$(ns_of "$AGENT")"
    RP="$(redis_pod)"; kubectl --context "$CTX" -n forgeos-data exec "$RP" -- redis-cli LPUSH "agent:$AGENT" wake >/dev/null
    kubectl --context "$CTX" -n "$NS" rollout status "deploy/$AGENT" --timeout=60s
    for i in $(seq 1 10); do [ -n "$(kubectl --context "$CTX" -n "$NS" get endpoints "$AGENT" -o jsonpath='{.subsets[0].addresses[0].ip}' 2>/dev/null)" ] && break; sleep 2; done
    kubectl --context "$CTX" -n "$NS" run "inv-$RANDOM" --rm -i --restart=Never --image=curlimages/curl:8.10.1 --quiet -- \
      curl -s -m 120 -X POST "http://$AGENT.$NS/invoke" -H 'Content-Type: application/json' -d "{\"prompt\": $(printf '%s' "$PROMPT" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')}"
    echo
    ;;
  *) echo "usage: $0 [show|wake|sleep|invoke <agent> <prompt>]"; exit 1;;
esac
