"""ForgeOS durable worker tier — an always-on GKE Deployment.

The per-turn runtime (FORGEOS_RUNTIME_WORKERS) processes agent runs off a Redis
Streams queue backed by the Postgres continuation store/ledger: it pulls a
runnable, drives ONE LLM turn, and re-enqueues the next turn (or parks on a
human approval and resumes later). That requires a worker that is ALWAYS
running — Cloud Run scale-to-zero can't guarantee a suspended HITL run ever
resumes — so the worker tier runs here as a GKE Deployment (replicas >= 1)
separate from the scale-to-zero platform-api Cloud Run service.

It runs the platform-api image with the worker flags; the worker pool starts via
the FastAPI lifespan (`--dashboard`), draining the shared Redis queue. Multiple
worker sources (this Deployment + any warm platform-api instance) coexist
safely: each process uses a unique Redis consumer name (host+pid, set in
src/runtime/service.py) and exactly-once is enforced by the ledger CAS.

Secrets: the app reads DATABASE_URL / REDIS_URL / the LLM provider key from the
ENVIRONMENT (bootstrap does not fetch Secret Manager itself), so we materialize
them into a k8s Secret synced from the same Pulumi sources platform-api uses.
(For stricter hygiene, swap this for the Secret Manager CSI driver or
external-secrets later — left as a follow-up to avoid adding cluster operators.)
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp
import pulumi_kubernetes as k8s


class WorkerTier(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        image: pulumi.Input[str],
        project: pulumi.Input[str],
        k8s_provider: k8s.Provider,
        agent_runtime_gsa: gcp.serviceaccount.Account,
        database_url: pulumi.Input[str],
        redis_url: pulumi.Input[str] | None,
        env_secrets: dict[str, pulumi.Input[str]] | None = None,
        kernel_mode: str = "production",
        syscall_pipeline: bool = True,
        replicas: int = 1,
        namespace: str = "forgeos-system",
        cpu: str = "500m",
        memory: str = "1Gi",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:worker:WorkerTier", name, None, opts)
        child = pulumi.ResourceOptions(parent=self, provider=k8s_provider)
        gcp_child = pulumi.ResourceOptions(parent=self)

        ksa_name = "forgeos-worker"

        # Dedicated namespace for infra workloads (kept apart from agent ns).
        ns = k8s.core.v1.Namespace(
            f"{name}-ns",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=namespace,
                labels={"forgeos.io/system": "true"},
            ),
            opts=child,
        )

        # KSA bound to the agent-runtime GSA via Workload Identity (for Secret
        # Manager / Cloud SQL access the app may need beyond the injected env).
        ksa = k8s.core.v1.ServiceAccount(
            f"{name}-ksa",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=ksa_name,
                namespace=ns.metadata.name,
                annotations={
                    "iam.gke.io/gcp-service-account": agent_runtime_gsa.email,
                },
            ),
            opts=child,
        )
        gcp.serviceaccount.IAMMember(
            f"{name}-wi",
            service_account_id=agent_runtime_gsa.name,
            role="roles/iam.workloadIdentityUser",
            member=pulumi.Output.concat(
                "serviceAccount:", project, ".svc.id.goog[", namespace, "/", ksa_name, "]"
            ),
            opts=gcp_child,
        )

        # k8s Secret with the env the app reads directly. Only include keys with
        # a value (REDIS_URL is None when Memorystore is disabled).
        string_data: dict[str, pulumi.Input[str]] = {"DATABASE_URL": database_url}
        if redis_url is not None:
            string_data["REDIS_URL"] = redis_url
        for k, v in (env_secrets or {}).items():
            string_data[k] = v

        # delete_before_replace: this Secret has a fixed name, so when its data
        # changes (e.g. a rotated DATABASE_URL) the default create-before-delete
        # collides with the existing object ("already exists"). Delete first.
        env_secret = k8s.core.v1.Secret(
            f"{name}-env",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="forgeos-worker-env",
                namespace=ns.metadata.name,
            ),
            string_data=string_data,
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider, delete_before_replace=True),
        )

        labels = {"app": "forgeos-worker", "forgeos.io/system": "worker"}

        plain_env = [
            k8s.core.v1.EnvVarArgs(name="FORGEOS_RUNTIME_V2", value="1"),
            k8s.core.v1.EnvVarArgs(name="FORGEOS_RUNTIME_WORKERS", value="1"),
            k8s.core.v1.EnvVarArgs(name="FORGEOS_SYSCALL_PIPELINE", value="1" if syscall_pipeline else "0"),
            k8s.core.v1.EnvVarArgs(name="GCP_PROJECT_ID", value=project),
        ]
        if kernel_mode:
            plain_env.append(k8s.core.v1.EnvVarArgs(name="FORGEOS_KERNEL_MODE", value=kernel_mode))

        self.deployment = k8s.apps.v1.Deployment(
            f"{name}-deploy",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="forgeos-worker",
                namespace=ns.metadata.name,
                labels=labels,
            ),
            spec=k8s.apps.v1.DeploymentSpecArgs(
                replicas=replicas,
                selector=k8s.meta.v1.LabelSelectorArgs(match_labels=labels),
                template=k8s.core.v1.PodTemplateSpecArgs(
                    metadata=k8s.meta.v1.ObjectMetaArgs(labels=labels),
                    spec=k8s.core.v1.PodSpecArgs(
                        service_account_name=ksa_name,
                        containers=[
                            k8s.core.v1.ContainerArgs(
                                name="worker",
                                image=image,
                                # --dashboard starts the FastAPI lifespan, which
                                # builds + starts the RuntimeService worker pool
                                # (FORGEOS_RUNTIME_WORKERS). No --loop: the
                                # company scheduler is platform-api's job, not the
                                # worker's. The served API has no Service/ingress.
                                # --no-auth is intentional: the worker drains the
                                # Redis queue and has NO Service/ingress, so its
                                # API is never reached by external role-gated
                                # calls. Do not "fix" this — it would demand a key
                                # the worker's internal callers don't send.
                                command=["python", "-m", "src.bootstrap"],
                                args=["--no-auth", "--dashboard", "--port", "8080"],
                                env_from=[
                                    k8s.core.v1.EnvFromSourceArgs(
                                        secret_ref=k8s.core.v1.SecretEnvSourceArgs(
                                            name=env_secret.metadata.name,
                                        ),
                                    )
                                ],
                                env=plain_env,
                                ports=[
                                    k8s.core.v1.ContainerPortArgs(name="http", container_port=8080),
                                ],
                                resources=k8s.core.v1.ResourceRequirementsArgs(
                                    requests={"cpu": cpu, "memory": memory},
                                    limits={"cpu": cpu, "memory": memory},
                                ),
                            )
                        ],
                    ),
                ),
            ),
            opts=pulumi.ResourceOptions(
                parent=self, provider=k8s_provider, depends_on=[ksa, env_secret],
                delete_before_replace=True,  # fixed name — delete before recreating on replace
            ),
        )

        self.namespace = ns
        self.register_outputs({})
