# ForgeOS Chaos Experiments

Production-grade chaos tests using [Chaos Mesh](https://chaos-mesh.org).
Each manifest defines a single experiment that exercises one failure mode
and has documented expected outcomes.

## Prerequisites

```bash
# Install Chaos Mesh in your cluster
curl -sSL https://mirrors.chaos-mesh.org/v2.6.0/install.sh | bash
```

This creates the `chaos-testing` namespace with the controller manager,
dashboard, and DNS server.

## Experiments

| File | Experiment | What it tests |
|------|-----------|---------------|
| `pod-failure.yaml` | Kill one API pod every 5 min for 30 min | HPA + PDB + invocation recovery |
| `network-delay.yaml` | 500ms latency + jitter on API pods | Streaming, tool timeouts, WS reconnect |
| `db-connection-kill.yaml` | Block egress to Postgres for 2 min | In-memory fallback + alert firing |
| `cpu-stress.yaml` | Pin one pod to 90% CPU for 5 min | Autoscaling + readiness probes |

## Running an experiment

```bash
# Apply — start the chaos
kubectl apply -f deploy/k8s/chaos/pod-failure.yaml

# Watch pods while chaos is running
kubectl get pods -n forgeos -w

# Remove — stop the chaos
kubectl delete -f deploy/k8s/chaos/pod-failure.yaml
```

## Expected outcomes per experiment

### pod-failure.yaml
- **Pod churn**: Every 5 minutes a random API pod dies.
- **Recovery**: Deployment should spin up a replacement within 30s.
- **Availability**: `minAvailable: 1` (PDB) should be honored — never 0 running pods.
- **Client impact**: HTTP requests in-flight on the killed pod should fail;
  retries from the frontend should land on a healthy pod.
- **Alerts**: None expected (single-pod death is normal cluster noise).

### network-delay.yaml
- **Latency**: API requests observe +500ms round-trip.
- **Streaming**: `/api/admin/chat/stream` should still emit tokens, just slower.
- **WebSocket**: `/ws/agents` reconnects if the delay exceeds the idle timeout.
- **Tool timeouts**: Tool executor has 60s default timeout; 500ms added
  latency should not breach it.
- **Alerts**: `ForgeOSSlowToolCalls` may fire if cumulative latency
  exceeds 30s P99.

### db-connection-kill.yaml
- **DB writes**: Client/MCP store writes fall back to in-memory.
- **DB reads**: In-memory cached rows still returned.
- **Status transitions**: `db.connection_lost` audit event fires → SEV1 alert.
- **Recovery**: Once partition heals, next write succeeds cleanly
  (no connection pool exhaustion).

### cpu-stress.yaml
- **Autoscaling**: HPA should scale up within 30s (target CPU 70%).
- **Probes**: Readiness probe may fail intermittently (6 failures = unready).
- **Liveness**: Should NOT restart the pod (3 failures × 30s = 90s window).
- **User impact**: Latency spikes on the affected pod.
- **Alerts**: May fire `ForgeOSSlowToolCalls` if sustained.

## Running all experiments in sequence

For a full chaos suite (requires staging environment — do NOT run against prod):

```bash
bash deploy/k8s/chaos/run-all.sh
```

Each experiment runs for its documented duration, then the next one starts.
Total run time: ~60 minutes.

## Safety

- **Never apply to prod.** Chaos experiments can kill pods and inject
  failures. Run against a dedicated staging cluster with synthetic load.
- **Disable PodChaos outside business hours** if you have automated
  escalations — unexpected pages are worse than no chaos tests.
- **Chaos Mesh's dashboard** at `http://localhost:2333` (via port-forward)
  shows experiment status and lets you stop early.
