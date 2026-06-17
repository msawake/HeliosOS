# Execution Plan — dual-target Helios OS (local → GCP), pyramid-first

Sequencing companion to `LOCAL_PARITY_PLAN.md` (architecture) and the approved
milestone plan. Principle: **build the cheap, high-fidelity layers first; let the
real GKE cluster we already have be the truth gate.** Invest local effort only
where it buys a faster loop.

## The test pyramid (cheapest, fastest at the bottom)

```
                    ┌─────────────────────────────────────────┐
            slow    │  P3 · PARITY GATE — real GKE Autopilot    │  truth
           $≈0 idle │  forgeos-autopilot-62a569f                │  literal gcp-pubsub
                    │  flip target=gcp; validate scale-from-0   │  scaler + real Pub/Sub
                    └─────────────────────────────────────────┘
              ┌───────────────────────────────────────────────────┐
     minutes  │  P1+P2 · LOCAL INTEGRATION — Docker Desktop k8s     │
              │  per-agent pods → KEDA scale 0↔N via PubSub         │  shape-accurate
              │  emulator + metrics-api + backlog-exporter          │  (no GCP creds)
              └───────────────────────────────────────────────────┘
        ┌─────────────────────────────────────────────────────────────┐
seconds │  P0 · FOUNDATION — NO CLUSTER                                 │  ~80% of bugs
        │  Pulumi mock tests · agent-base runtime · docker-compose      │  die here
        └─────────────────────────────────────────────────────────────┘
```

## One program, two stacks (the dual-target spine)

```
                          pulumi/  (ONE program)
                          forgeos:target
                   ┌────────────────┴────────────────┐
               target=local                      target=gcp
        k8s provider → Docker-Desktop k8s   k8s provider → GKE Autopilot
        Postgres/Redis = pods (Helm)        Cloud SQL / Memorystore
        secrets = k8s Secrets               Secret Manager
        api/mc = Deployment+Service         Cloud Run
        Pub/Sub = emulator pod              real Pub/Sub
        network/registry/identity = no-op   VPC / Artifact Registry / GSAs
     ───────────────────────── identical below ─────────────────────────
        per-agent Deployment  +  metrics-api ScaledObject  +  backlog-exporter
```

## Trigger + autoscale wiring (identical on both targets)

```
  /invoke ──publish(attributes.agent=X)──▶  Pub/Sub topic ──▶ sub:X ──▶ agent-X pod
                                                  │                    (consumer →
                                       backlog-exporter ◀── peek ──────  run_agentic_loop
                                                  │ /backlog?sub=X        → ack)
                                                  ▼ (JSON via GJSON path)
                                       KEDA metrics-api scaler ──▶ scale agent-X  0↔N
```

Only the *backlog source* differs (emulator local / real Pub/Sub gcp); the
ScaledObject stanza, the topic/subscription model, and the consumer are the same
everywhere. The deprecated `gcp-pubsub` scaler is avoided entirely.

---

## Sequence

### P0 — Foundation (no cluster) · start now, fully parallel
| # | Deliverable | Files | Gate |
|---|-------------|-------|------|
| P0.1 | **agent-base runtime** — long-running HTTP `POST /invoke` → `run_agentic_loop`, tool-proxy to platform, `/healthz` | `src/agent_runtime/server.py`, `infrastructure/docker/Dockerfile.agent-base` | `docker run` + `POST /invoke` drafts a letter on Gemini |
| P0.2 | **Pulumi mock-test harness** — assert per-agent graph (N agents → N Deployments + N ScaledObjects; each ns has quota+netpol) | `pulumi/tests/test_agent_graph.py` (uses `pulumi.runtime.set_mocks`) | `pytest pulumi/tests` green, **no infra** |
| P0.3 | **docker-compose smoke** — platform + Postgres + Redis + a pod-less agent run | reuse the top-level `docker-compose.yaml` | `forgeos health` ok; an agent invocation runs |

### P1 — Local integration: dual-target spine + per-agent pods (Docker Desktop k8s)
`forgeos:target` switch + `components/providers.py`; local branches for
`data.py` / `secrets.py` / new `controlplane_k8s.py`; no-op
network/registry/identity/observability; `agent_base.py` M1 mode (fixed
`replicas:1`, no scaler yet). **Gate:** `pulumi up -s local` → control plane +
data + namespaces + one pod per agent the platform dispatches to over HTTP; the
`examples/law-firm/TESTING.md` beats pass against the cluster.

### P2 — Local autoscale: Pub/Sub emulator + metrics-api + exporter
Emulator Deployment + topic/subscriptions; `trigger_transport.py` (publisher +
consumer, honors `PUBSUB_EMULATOR_HOST`); `backlog-exporter`; `agent_base.py`
metrics-api ScaledObject (min=0 event/scheduled, 1 always_on). **Gate:** publish
→ KEDA scales a pod 0→1→0 (`kubectl get pods -n legal -w`).

### P3 — Parity gate: real GKE (already live)
Flip `Pulumi.dev.yaml` `target: gcp`, push images to Artifact Registry, populate
`agents:`, `pulumi up -s gcp`; validate scale-from-zero on
`forgeos-autopilot-62a569f`. **Gate:** `make parity` green on both targets; diff
between targets = only `Pulumi.<stack>.yaml`.

---

## Why this order
P0 retires the dual-target *graph* risk and the *runtime* risk with zero infra —
most bugs surface in `pytest`/compose in seconds. P1/P2 prove the pod + autoscale
*mechanics* on the k8s you already have enabled (no new install, no GCP creds).
P3 uses the real cluster (scale-to-zero ≈ free) as the fidelity gate instead of
over-engineering local emulation. Each phase is independently demoable and
revertable (`FORGEOS_DISPATCH=inproc` default; `pulumi destroy -s local` is free).

## Starting move
P0.1 (agent-base runtime) and P0.2 (Pulumi mock tests) are infra-free and
parallel — they unblock everything and let the cluster choice wait until P1.
