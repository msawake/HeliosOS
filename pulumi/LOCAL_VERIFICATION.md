# Local verification results — dual-target ForgeOS (kind), law-firm fleet

What was built and **verified locally** (kind cluster, real Gemini, zero GCP creds),
following `EXECUTION_PLAN.md`. The full pyramid P0→P2 is green.

## P0 — Foundation (no cluster)
- **P0.1 agent-base runtime** — `src/agent_runtime/server.py` (long-running HTTP
  `POST /invoke` → `run_agentic_loop`, `/healthz`) + `infrastructure/docker/Dockerfile.agent-base`.
  Verified: `docker run` + `POST /invoke` drafts an engagement letter on Gemini.
- **P0.2 Pulumi mock tests** — `pulumi/tests/test_agent_graph.py` (`set_mocks`).
  **4 passed**: N agents → N Deployments + N Subscriptions + N ScaledObjects, per-agent
  subscription filter, replicas reflect `always_on`. (Exposed `scaledobject` on
  `AgentWorkload` to make the graph assertable.)
- **P0.3 data tier + app logic** — compose Postgres+Redis up; law-firm intake beat
  green on the no-cluster platform (clerk now prefers caller-provided records →
  `clear` → letter → approval gate).

## P1 — Dual-target spine + per-agent pods (local k8s)
- **One program, two stacks:** `__main__.py` dispatches on `forgeos:target`;
  GCP body preserved verbatim in `gcp_stack.py`; new `local_stack.py` +
  `components/agent_local.py` (LocalAgentWorkload = Deployment + Service, M1 mode).
  `Pulumi.local.yaml` (stack `local`, passphrase secrets, the 4 law-firm agents).
- `providers.py`-style k8s provider from the kind context; reads each agent's real
  system prompt from `examples/law-firm/*/system_prompt.md`.
- Verified: `pulumi up -s local` → **16 resources, zero GCP calls**; 4 agents as
  **per-agent pods across `legal`/`conflicts`** (ethical-wall topology); in-cluster
  `POST http://law-firm-associate.legal/invoke` → real Gemini letter;
  `POST http://conflicts-clerk.conflicts/invoke` → `VERDICT: conflict`.

## P2 — KEDA autoscale (scale-to-zero)
- KEDA installed (helm); in-cluster Redis + a per-agent **KEDA ScaledObject**
  (`min=0`), program-driven by `local_stack.py` (`enableAutoscale`).
- Verified: idle → all agents **scale to 0**; backlog item → associate **0→1 in ~2s**;
  drain → **1→0** after cooldown; and a **scaled-from-zero pod served a real Gemini
  letter** over in-cluster HTTP (combined P1+P2 proof).

## P1b — pods connected to Drive (via host-platform proxy)
The kind pods now reach real Google Drive. Each pod runs the agent-base runtime
with `AGENT_TOOLS` = its `drive__*`/`memory__*`/`company__*` tools and
`FORGEOS_API_URL=http://host.docker.internal:5000` (+ a `hostAliases` entry so
pods resolve the host). At startup the pod **registers** with the platform
(`POST /api/sandbox/register`) for a scoped token, then **proxies** each tool call
to `POST /api/sandbox/tool`, where the host platform executes it with the
service account (keyless SA impersonation via the host's ADC).
- Verified: pod `POST /invoke "list the files you can access in Drive"` →
  `drive__list_files` proxied → **real Drive files** (forge_folder, ForgeOS Drive
  Demo, …). And `find_by_name`+`read_file` → **real document content**.
- Platform fixes required (all pre-existing bugs in never-exercised routes):
  `/api/platform/tools` 500 (`tool_executor` not in scope) and `/api/sandbox/tool`
  500 (`platform_kernel` undefined) → added `_resolve_tool_executor()`; added
  `SandboxTokenStore.mint_for` + `POST /api/sandbox/register`; sandbox tool calls
  execute on the token's scoped whitelist (no per-agent registry binding needed).
- On GKE the same path uses **Workload Identity** (the pod's KSA → the drive SA)
  instead of host-ADC + host.docker.internal.

## P1.7 — `forgeos invoke` routes to the pods (CLI front door)
`forgeos invoke <id>` now executes **in the agent's pod**, not in-process. The
forgeos adapter forwards `/invoke` to a pod's HTTP Service when the agent's
`metadata.pod_service_url` is set (`stacks/forgeos/adapter.py::_forward_to_pod`).
`scripts/local/cli-wire.sh` wakes each pod, port-forwards its Service to a
localhost port, and deploys a pod-backed forwarder shell into the platform.
- Verified: `forgeos invoke <associate>` → platform → pod → `drive__list_files`
  (proxied to SA) → **real Drive files**; `forgeos invoke <conflicts-clerk>` →
  `VERDICT: conflict`. Full chain: CLI → Platform API → pod → (Drive proxy) → SA.
- Local-only detail: the host platform can't reach a kind ClusterIP, so we
  port-forward. On GKE (platform in-cluster) the forwarder URL is the Service DNS
  directly — no port-forward.

## Honest deviation (transport)
The approved plan's P2 transport is **Pub/Sub-emulator + metrics-api scaler +
backlog-exporter** (for app-fidelity with GCP). For a fast, reliable *local proof
of the autoscaling mechanism*, P2 here uses KEDA's native **`redis` scaler** on a
Redis list backlog — no app change, no exporter. The **scale-to-zero behavior is
proven**; swapping the trigger to the Pub/Sub-backed metrics-api path (so the app
transport is identical local↔GCP) is the remaining P2 work and the P3/GCP target.
Pods now proxy **`drive__*`/`memory__*`/`company__*`** to the host platform (P1b);
**A2A (`agent__call`) and `human__*`** remain excluded in-cluster (callee peers /
A2H gateway not wired across pods yet).

## Reproduce
```bash
kind create cluster --name forgeos
docker build -f infrastructure/docker/Dockerfile.agent-base -t forgeos/agent-base:dev .
kind load docker-image forgeos/agent-base:dev --name forgeos
helm install keda kedacore/keda -n keda --create-namespace --kube-context kind-forgeos --wait
cd pulumi && export PULUMI_CONFIG_PASSPHRASE=forgeos-local
pulumi stack select local
GEMINI_API_KEY=$(grep '^GEMINI_API_KEY=' ../.env|cut -d= -f2-|tr -d '"') pulumi up -s local --yes
pulumi/venv/bin/python -m pytest pulumi/tests -q          # 4 passed
# scale-from-zero demo:
RPOD=$(kubectl --context kind-forgeos -n forgeos-data get pod -l app=forgeos-redis -o jsonpath='{.items[0].metadata.name}')
kubectl --context kind-forgeos -n forgeos-data exec $RPOD -- redis-cli LPUSH agent:law-firm-associate trigger
kubectl --context kind-forgeos -n legal get deploy law-firm-associate -w   # 0 -> 1
```

## Remaining (not local)
- **P3 — real GKE parity** (`target=gcp`): push images to Artifact Registry,
  `pulumi up -s gcp`, validate scale-from-zero on `forgeos-autopilot-62a569f`.
- **Transport fidelity** — Pub/Sub-emulator + metrics-api + backlog-exporter + the
  agent-base consumer mode, so the trigger path is identical local↔GCP.
