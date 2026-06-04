# Plan: one Pulumi program, two targets — local-first, then GCP

## Goal

Run **`pulumi up` against a local Kubernetes cluster** and get the same behavior
we proved by hand (per-agent pods, scale-to-zero, namespaces/quotas/ethical-wall,
a control plane talking to Postgres/Redis) — then point the **same Pulumi
program** at GCP and get the GKE/Cloud Run version with no code changes, only a
stack/config switch.

Principle: **one program, two stacks, a `target` switch.**

```
pulumi stack select local   # forgeos:target = local  → kind + in-cluster Postgres/Redis + k8s control plane
pulumi stack select gcp     # forgeos:target = gcp    → GKE Autopilot + Cloud SQL + Cloud Run (today's stack)
```

The local stack is the fast inner loop; GCP is the same graph with managed
backends swapped in. Anything that can't be identical is hidden behind a thin
interface so the *agent/namespace/scaling* layer is byte-for-byte the same.

---

## Why this is mostly easy — and the one hard seam

From the component inventory, almost every GCP resource has a clean local
substitute, and the **per-agent workload layer is already pure Kubernetes**
(`agent_base.py`, `namespaces.py`, `keda.py`, `observability.py` all use the k8s
provider). Those run unchanged on a local cluster.

| GCP resource (today) | Local substitute | Notes |
|---|---|---|
| GKE Autopilot (`gke.py`) | **kind** (or k3d) cluster + `k8s.Provider` from its kubeconfig | only the provider construction differs |
| Cloud SQL Postgres (`data.py`) | **Postgres** via Bitnami Helm (or a Deployment) in-cluster | emit the same `database_url` output shape |
| Memorystore Redis | **Redis** via Bitnami Helm in-cluster | same `redis_url` shape |
| Artifact Registry (`registry.py`) | **kind image load** (or a local `registry:2`) | `kind load docker-image …`; image refs become local |
| Cloud Run: Platform API, Mission Control (`platform_api.py`, `mission_control.py`) | **k8s Deployment + Service** (ClusterIP + port-forward / NodePort) | same container, env, ports (5000 / 8080) |
| Cloud Run Job migrations (`migrations.py`) | **k8s Job** | same image, `DATABASE_URL` env |
| Secret Manager (`secrets.py`) | **k8s Secrets** | same env var names mounted into pods |
| GSAs + IAM + Workload Identity (`identity.py`, WI annotations in `namespaces.py`) | **omit** — plain KSA, no WI annotation | env-injected creds instead of WI |
| VPC/subnet/NAT/PSA (`network.py`) | **omit** (kind has its own net) | no-op on local |
| Managed Prometheus `PodMonitoring` (`observability.py`) | **omit or swap** to a `ServiceMonitor` (kube-prometheus) | GCP-only CRD; guard behind `target` |
| **KEDA `gcp-pubsub` scaler** (`agent_base.py`) | **the one real problem** — see below | emulator can't satisfy the gcp-pubsub scaler |

### The seam: the trigger + autoscale path

Today: Platform API publishes to the `agent_triggers` Pub/Sub **topic** with
`attributes.agent=<name>` → each agent has a **subscription** filtered to its
name → **KEDA `gcp-pubsub` scaler** polls that subscription's backlog every 15s →
scales the agent Deployment `0..N`.

The KEDA `gcp-pubsub` scaler calls the **real** `pubsub.googleapis.com` with GCP
credentials. **It cannot talk to the Pub/Sub emulator.** So the emulator alone
gives us message transport but **no autoscaling** locally. That seam is the only
thing the plan must actively design around; everything else is substitution.

**Decision — abstract the trigger transport behind a `target`-aware interface:**

- **gcp:** Pub/Sub topic + per-agent subscription + KEDA `gcp-pubsub` scaler *(unchanged from today)*.
- **local:** **Redis** (already a platform dependency) as the queue — one Redis
  list/stream per agent — + KEDA's built-in **`redis`/`redis-streams` scaler**,
  which runs fully locally with no cloud auth and scales on real backlog.

This keeps the *behavior* identical (queue backlog drives replicas, scale-to-zero)
with a real KEDA scaler in both worlds. The cost is a small **app-code** addition:
the publisher and the agent runtime must speak a Redis transport in addition to
Pub/Sub, selected by env (`FORGEOS_TRIGGER_TRANSPORT=redis|pubsub`). That is the
only application change the plan requires; it's a clean parallel to the existing
Pub/Sub path.

> Rejected alternatives: (a) Pub/Sub **emulator + KEDA gcp-pubsub** — scaler can't
> auth to the emulator; (b) emulator + a custom backlog-exporter feeding KEDA
> `metrics-api` — the emulator doesn't expose `numUndeliveredMessages`, so the
> exporter is fragile. Redis is already in the stack and KEDA supports it natively.

---

## Phased plan

### Phase 0 — Local cluster & images (no Pulumi changes)
1. Tooling: `kind`, `kubectl`, `helm`, the existing `pulumi` CLI + venv.
2. `kind create cluster --name forgeos` (1 control-plane + 1 worker; pin a k8s
   version close to GKE's 1.3x).
3. Build the four images for **linux/amd64** (so the same tags work on GKE) and
   load them: `platform-api`, `mc`, `agent-base` (the `forgeos-sandbox`/runner
   image we already built is the agent runtime), `migrations`.
   `kind load docker-image forgeos/agent-base:dev …`
4. **Exit criteria:** `kubectl get nodes` healthy; images present in the node.

### Phase 1 — Make the Pulumi program dual-target (the core refactor)
Introduce `target` and branch each substitutable component. Keep **one**
`__main__.py`; add `Pulumi.local.yaml`.

1. **Config & stacks**
   - `pulumi stack init local`; `Pulumi.local.yaml` sets `forgeos:target: local`,
     `namespaces: [legal, operations, sales-team]`, an `agents:` list (see
     Phase 3), and image tags = the locally-loaded `:dev` tags.
   - Existing `Pulumi.dev.yaml` gets `forgeos:target: gcp`.
2. **Provider factory** — a `components/providers.py` returning the right
   `k8s.Provider`: local reads `~/.kube/config` (kind context); gcp keeps
   `gke.py`. `network.py`/`registry.py`/`identity.py` become **no-ops when
   `target==local`** (return stub outputs).
3. **Data** — `data.py` grows a local branch: deploy Bitnami `postgresql` and
   `redis` Helm releases into a `data` namespace; synthesize `database_url` /
   `redis_url` from in-cluster Service DNS (`…svc.cluster.local`). GCP branch
   unchanged. Output **shapes** identical so downstream code doesn't care.
4. **Secrets** — `secrets.py` local branch creates **k8s Secrets** (same keys:
   `DATABASE_URL`, `GEMINI_API_KEY`, …) instead of Secret Manager; downstream
   reads them as `secretKeyRef` env.
5. **Control plane** — add `components/controlplane_k8s.py`: `Deployment+Service`
   for Platform API (`:5000`) and Mission Control (`:8080`), and a `Job` for
   migrations — used when `target==local`; `platform_api.py`/`mission_control.py`
   (Cloud Run) used when `target==gcp`.
6. **Namespaces/KEDA/Observability** — reuse as-is, but: drop the WI annotation on
   the KSA when local; install KEDA via the existing `keda.py` Helm release (works
   on kind); guard the `PodMonitoring` CRD behind `target==gcp` (or swap to a
   `ServiceMonitor` only if kube-prometheus is installed).
7. **Exit criteria:** `pulumi up` on the `local` stack brings up the control plane
   + Postgres + Redis + KEDA + the three namespaces with quotas + default-deny
   netpol — and `forgeos health` (via port-forward to the Platform API Service)
   returns `status: ok`. No agents yet.

### Phase 2 — Per-agent pods that actually autoscale locally
1. **Trigger transport interface (app code).** Add `FORGEOS_TRIGGER_TRANSPORT`
   (`pubsub` default, `redis` local). Implement a Redis transport: publisher
   `LPUSH agent:<name> <payload>`; agent runtime `BRPOP`s its key. Mirror the
   existing Pub/Sub publish/consume paths; one small module + a factory.
2. **`agent_base.py` — target-aware scaler.** Keep the Deployment identical. The
   `ScaledObject` trigger becomes:
   - gcp: `type: gcp-pubsub` (today).
   - local: `type: redis` (or `redis-streams`), `metadata.listName: agent:<name>`,
     `listLength: "5"`, `address: <redis Service>:6379`.
   `min/maxReplicaCount`, polling, cooldown unchanged. Subscription resource
   (Pub/Sub) is created only when `target==gcp`.
3. **Exit criteria:** with `agents:` populated, `pulumi up` creates one Deployment
   + ScaledObject per agent; event/scheduled agents sit at **0 replicas**;
   `LPUSH agent:<name>` (or a Platform API trigger) makes **KEDA scale the pod to
   1**, the agent processes and acks, then **scales back to 0**. `always_on`
   agents hold at 1.

### Phase 3 — Wire the law-firm fleet as the workload
1. Populate `agents:` in `Pulumi.local.yaml` with the four agents, mapped to the
   provisioned namespaces (associate/risk/docketing → `legal`; conflicts-clerk →
   add `conflicts` to the `namespaces` list, or co-locate in `legal` and rely on
   the A2A ACL for the wall):
   ```yaml
   forgeos:agents:
     - { name: law-firm-associate,      namespace: legal,     always_on: false, manifest_ref: "k8s-secret://manifests/associate" }
     - { name: conflicts-clerk,         namespace: conflicts, always_on: false, manifest_ref: "…" }
     - { name: risk-compliance-auditor, namespace: legal,     always_on: false, manifest_ref: "…" }
     - { name: docketing-clerk,         namespace: legal,     always_on: false, manifest_ref: "…" }
   ```
   Locally, `manifest_ref` resolves from a ConfigMap/Secret (GCS only on gcp).
2. **Exit criteria:** the same end-to-end beats from `examples/law-firm/TESTING.md`
   run against the cluster: trigger the associate → its pod scales up, runs the
   intake (A2A to conflicts-clerk → its pod scales up across the namespace wall),
   opens the approval gate, scales back to 0. Verify with
   `kubectl get pods -n legal -w` and `kubectl get scaledobject -A`.

### Phase 4 — Promote to GCP (same program)
1. `pulumi stack select gcp` (today's `dev` stack, 111 resources, cluster already
   live) → set the same `agents:` list (with `gs://…` manifest refs and the
   registry image tags) → `pulumi up`.
2. **Exit criteria:** the four agents appear as GKE Deployments + `gcp-pubsub`
   ScaledObjects in the `legal`/`conflicts` namespaces; a Pub/Sub trigger scales a
   pod from zero; `kubectl get pods -n legal` (against `forgeos-autopilot-62a569f`)
   shows the same lifecycle we just saw locally. The diff between local and gcp is
   **only** `Pulumi.<stack>.yaml`.

### Phase 5 — Parity guardrails
1. A `make parity` script that, for whichever stack is selected, asserts the
   invariants: N agent Deployments == len(agents); each has a ScaledObject; each
   namespace has the quota + default-deny netpol; control plane health is ok.
2. Document the known, intentional differences (WI vs env creds; Cloud Run vs
   k8s Deployment; gcp-pubsub vs redis scaler; Managed Prometheus vs none) in a
   short "local ≠ prod" table so nobody mistakes a substitution for a bug.

---

## Risks & realities (so nothing surprises us)
- **Trigger transport is the only app change.** Everything else is infra
  substitution. Budget the Redis transport as a real (small) code task with tests.
- **Image arch:** build `--platform linux/amd64` so the *same* images run on kind
  (Docker Desktop on Apple Silicon emulates) and GKE. Avoid `:latest` drift —
  use immutable `:dev-<sha>` tags loaded into kind and pushed to Artifact Registry.
- **Autopilot-only behaviors** won't exist on kind: no Workload Identity, no
  Managed Prometheus, NetworkPolicy egress semantics differ. All are guarded by
  `target` and listed in the "local ≠ prod" table — they are *expected* gaps.
- **Pulumi secrets:** the local stack should use a passphrase secrets provider
  (`PULUMI_CONFIG_PASSPHRASE`) or `--secrets-provider passphrase`, independent of
  the gcp stack's provider, so contributors can `pulumi up` locally without GCP.
- **`registry.py` reads a pre-existing repo** (`.get`) — must be a no-op locally
  or it will try to read a GCP repo; gate it on `target`.
- **manifest_ref** indirection differs (GCS vs ConfigMap) — abstract behind the
  same env var the agent runtime already reads (`FORGEOS_AGENT_MANIFEST`).
- **Cost/safety:** the local path needs **zero** GCP credentials once images are
  built — that's the whole point; keep it that way (no real Pub/Sub fallback).

## Definition of done
- `pulumi up` on `local` → control plane + data + KEDA + namespaces + 4 agent
  Deployments scaling 0↔N on Redis backlog, on a kind cluster, no GCP creds.
- `pulumi up` on `gcp` → the identical graph on GKE Autopilot + Cloud Run +
  Cloud SQL + real Pub/Sub, differing only by `Pulumi.<stack>.yaml`.
- `examples/law-firm/TESTING.md` beats pass against **both** targets.
- `make parity` green on both.

## Suggested order of execution
Phase 0 → 1 (infra shape, no agents) is the fast, high-confidence win and proves
the dual-target spine. Phase 2 (Redis transport + scaler) is the real engineering.
Phase 3 reuses the law-firm fleet. Phase 4 is a config flip. Do 0–1 first and
demo scale-to-zero of the control plane before investing in the transport.
