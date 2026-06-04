#!/usr/bin/env bash
# P1.7 — make `forgeos invoke <id>` reach the law-firm PODS (not in-process).
# For each agent: wake its pod, port-forward its Service to a fixed localhost
# port, and deploy a pod-backed "forwarder" shell into the host platform whose
# invoke forwards to that port. Then `forgeos invoke <id> "..."` flows
# CLI -> Platform API -> pod (and the pod proxies drive__* back for Drive).
#
# Local-only: the host platform can't reach a kind ClusterIP, so we port-forward.
# On GKE (platform in-cluster) the forwarder URL is the Service DNS directly.
set -euo pipefail
CTX="kind-forgeos"
AGENTS=(law-firm-associate conflicts-clerk risk-compliance-auditor docketing-clerk forgeos-lens-builder)
ns_of() { case "$1" in conflicts-clerk) echo conflicts;; forgeos-lens-builder) echo forge-lens;; *) echo legal;; esac; }
port_of() { case "$1" in law-firm-associate) echo 18081;; conflicts-clerk) echo 18082;; risk-compliance-auditor) echo 18083;; docketing-clerk) echo 18084;; forgeos-lens-builder) echo 18085;; esac; }

RPOD="$(kubectl --context "$CTX" -n forgeos-data get pod -l app=forgeos-redis -o jsonpath='{.items[0].metadata.name}')"
TMP="$(mktemp -d)"
echo "Wiring forgeos CLI -> pods (port-forwards + forwarder agents)..."
for a in "${AGENTS[@]}"; do
  ns="$(ns_of "$a")"; port="$(port_of "$a")"
  # 1) wake the pod so the Service has an endpoint (leaves a backlog item, so
  #    KEDA keeps it at >=1 while you use the CLI).
  kubectl --context "$CTX" -n forgeos-data exec "$RPOD" -- redis-cli LPUSH "agent:$a" wake >/dev/null
  kubectl --context "$CTX" -n "$ns" rollout status "deploy/$a" --timeout=60s >/dev/null
  # 2) port-forward the Service to a stable localhost port (background, survives this
  #    script). kubectl port-forward sometimes exits if the endpoint isn't ready yet,
  #    so verify /healthz and retry a few times.
  pkill -f "port-forward.*svc/$a " 2>/dev/null || true; sleep 1
  for attempt in 1 2 3; do
    nohup kubectl --context "$CTX" -n "$ns" port-forward "svc/$a" "$port:80" >/dev/null 2>&1 &
    disown || true
    ok=""
    for _ in 1 2 3 4 5; do
      sleep 1
      if curl -s -m 3 -o /dev/null "http://localhost:$port/healthz"; then ok=1; break; fi
    done
    [ -n "$ok" ] && break
    echo "  (port-forward for $a not ready, retry $attempt)"
    pkill -f "port-forward.*svc/$a " 2>/dev/null || true; sleep 1
  done
  # 3) deploy a pod-backed forwarder shell into the host platform (idempotent:
  #    drop any existing same-name agent first, since the registry rejects dups).
  existing="$(forgeos list 2>/dev/null | awk -v n="$a" '$2==n{print $1}')"
  [ -n "$existing" ] && forgeos undeploy "$existing" >/dev/null 2>&1 || true
  # The A2A handler reads the callee's ACL from the registry record's
  # metadata._capabilities (read_v2_section). The Conflicts Clerk lives behind an
  # ethical wall (its own `conflicts` namespace) — by default NO peer outside that
  # namespace can call it. We open one governed door: the legal team (where the
  # Associate runs) may cross. Any other namespace is denied by the A2A layer.
  caps='{ pod_service_url: "http://localhost:'"$port"'" }'
  if [ "$a" = "conflicts-clerk" ]; then
    caps='{ pod_service_url: "http://localhost:'"$port"'", _capabilities: { a2a: { canBeCalledBy: [ { namespace: legal } ], max_depth: 3 } } }'
  fi
  cat > "$TMP/$a.yaml" <<YAML
apiVersion: forgeos/v1
kind: Agent
metadata: { name: $a, namespace: $ns }
spec:
  stack: forgeos
  execution_type: reflex
  ownership: shared
  llm: { chat_model: gemini-2.5-pro, provider: google }
  system_prompt: { content: "Pod-backed forwarder for $a — invokes route to its Kubernetes pod." }
  metadata: $caps
YAML
  forgeos deploy "$TMP/$a.yaml" 2>&1 | sed 's/^/  /'
done
echo
echo "=== forgeos list (now CLI-invokable; invoke forwards to the pod) ==="
forgeos list
echo
echo "Try:  forgeos invoke \$(forgeos list | awk '/law-firm-associate/{print \$1}') \"list the files you can access in Google Drive\""
echo "Stop port-forwards:  pkill -f 'kubectl.*port-forward'"
