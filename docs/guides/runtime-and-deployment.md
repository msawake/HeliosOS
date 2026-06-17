# Runtime & Deployment — where does the agent actually run?

The manifest is declarative. The question this page answers is: *given an `AgentContract`, what process executes the agent code, and where does that process live?*

Short answer: it depends on **how you deployed the platform**. There are three realistic modes today.

---

## Mode 1 — Local dev (Python process)

When you boot the platform with `--no-auth` and deploy an agent via the CLI:

```bash
PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000
forgeos deploy examples/sre-gcp-auditor/manifest.yaml
```

The agent runs **in the same Python process as the platform**. The flow:

1. `ForgeOSClient.deploy()` POSTs the manifest to `/api/platform/agents`.
2. `src/platform/executor.py` registers the `AgentDefinition` and creates an `AgentProcess` (PID, phase machine, resource accounting).
3. For `lifecycle.type: scheduled`, `src/platform/scheduler.py` registers the cron with APScheduler (requires `pip install -e ".[scheduler]"`).
4. When the cron fires, the executor calls the stack adapter (e.g. `stacks/adk/adapter.py`), which runs `agent.py`'s entrypoint as an asyncio task.
5. Tool calls round-trip through the in-process kernel (`src/platform/kernel.py` + `src/platform/syscall.py`).

No containers, no pods. This is the fastest loop for development.

---

## Mode 2 — In-platform on a server (still one process)

The exact same code path as Mode 1 — but deployed to a long-running host (a VM, a Cloud Run service running the platform itself). The platform process owns the scheduler, registry, executor, and the agent's asyncio task. This is how the [Mission Control deployment](../operations/deployment.md) currently runs.

Trade-off: simple, but every agent shares the platform's CPU/memory. Fine for tens of lightweight agents; not the model for heavy or untrusted workloads.

---

## Mode 3 — Containerized per-agent (Cloud Run)

This is what `spec.runtime.image: forgeos-sre-gcp-auditor:latest` is hinting at.

For a `scheduled` agent like `sre-gcp-auditor`, the production shape is:

```
Cloud Scheduler  ──(HTTPS @ 06:00 UTC daily)──▶  Cloud Run Job
                                                       │
                                                       ▼
                                        Container: forgeos-sre-gcp-auditor:latest
                                          ├─ runs examples/sre-gcp-auditor/agent.py
                                          └─ uses runtime SDK to talk to platform
                                                       │
                                                       ▼
                                       Platform service (kernel, audit, A2A)
                                       on a separate Cloud Run service
```

The auditor's `agent.py` connects back via `FORGEOS_API_URL` to the platform's HTTP kernel for `check_tool`, `audit`, `ask_human`, `checkpoint`. That's the **Mode C** runtime in `Runtime.from_env()` — remote kernel, in-container agent.

For `always_on` agents, the equivalent is a Cloud Run *service* (not a job) with `min_instances=1`.

> The repo already deploys Mission Control to Cloud Run (see commit `fc76c1cc feat: deploy Mission Control to Cloud Run`). Per-agent images follow the same pattern; see `infrastructure/terraform/gcp/` for the IaC.

---

## How manifest fields map to the actual runtime

The manifest borrows Kubernetes vocabulary, but Helios OS does not run on Kubernetes today. Here is the actual mapping:

| Manifest field            | Local dev                    | In-platform                  | Cloud Run                                   |
|---------------------------|------------------------------|------------------------------|---------------------------------------------|
| `runtime.framework`       | Picks the adapter import     | Same                         | Same — burned into the container image      |
| `runtime.image`           | Ignored                      | Ignored                      | Tag of the Cloud Run revision                |
| `lifecycle.type: scheduled` + `schedule` | APScheduler in-process | APScheduler in-process | Cloud Scheduler → Cloud Run job              |
| `lifecycle.type: always_on` | asyncio task in platform   | asyncio task in platform     | Cloud Run service, `min_instances=1`         |
| `lifecycle.type: event_driven` | Event bus subscription  | Event bus subscription       | Pub/Sub push → Cloud Run service             |
| `lifecycle.replicas`      | Always 1                     | Always 1                     | `max_instances` for the Cloud Run service    |
| `lifecycle.restart_policy: OnFailure` | asyncio retry      | asyncio retry                | Cloud Run job retry policy                   |
| `boundaries.budgets.*`    | Enforced by in-proc kernel   | Enforced by in-proc kernel   | Enforced by remote kernel HTTP call          |
| `capabilities.tools.*`    | Enforced before dispatch     | Same                         | Same — `runtime.check_tool()` over HTTP      |
| `dependencies.agents`     | Admission check on deploy    | Same                         | Same                                         |

Two things worth internalizing:

1. **`replicas` and `restart_policy` are aspirational k8s-style vocabulary.** They get mapped onto whatever deployment target you use. There is no `kubelet` actuating them today.
2. **The kernel runs wherever the platform runs**, not next to the agent. In Cloud Run mode, the agent makes HTTP calls back to the platform for every governance check. Latency budget: ~5–20 ms per check.

---

## Choosing a mode

| You are... | Use |
|------------|-----|
| Developing a new agent | Mode 1 (local Python) |
| Running a handful of trusted internal agents | Mode 2 (in-platform) |
| Running scheduled jobs or untrusted-ish workloads at org scale | Mode 3 (Cloud Run per-agent) |
| Running heavy multi-agent crews with isolation requirements | Mode 3, or the `sandbox` stack (Docker per-invocation) — see `stacks/sandbox/adapter.py` |

---

## What is *not* used today

- **Kubernetes / kubelets / pods.** The manifest is k8s-shaped because the team plans to support a Kubernetes controller later (see `deploy/k8s/`), but the *current* controller is the in-process executor + scheduler. The k8s manifests in the repo are scaffolding, not the active path.
- **Per-agent VMs.** Sandbox isolation, when needed, is per-invocation Docker via `stacks/sandbox/adapter.py`, not long-lived VMs.

---

## See also

- [Stack Adapters](../architecture/stack-adapters.md) — what each framework adapter does.
- [Platform Layer](../architecture/platform-layer.md) — scheduler, executor, registry.
- [Process Table](../architecture/process-table.md) — `AgentProcess`, checkpoint/restore.
- [Deployment](../operations/deployment.md) — current production deployment steps.
