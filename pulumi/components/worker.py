"""Helios OS worker tier — always-on GKE Deployments: Celery worker + beat.

Post-cutover the Django web layer (platform-api) does NOT execute agents inline;
it enqueues `forgeos.run_agent` / `forgeos.resume_agent` on the Redis broker and
polls. This tier runs the Celery workers that consume those queues
(`agents,agents_resume,scheduled,agents_longrun`). Each worker process boots the
platform once (worker_process_init) — so it holds the executor + MCP + the
runtime engine the inline turn and HITL resume use — which is why it must be
ALWAYS running (Cloud Run scale-to-zero can't guarantee a parked HITL run ever
resumes), hence a GKE Deployment (replicas >= 1) rather than the scale-to-zero
platform-api Cloud Run service.

`FORGEOS_RUNTIME_WORKERS=1` is still set so `_maybe_build_runtime_service` wires
the durable engine/ledger that `resume_agent` drives. A second **beat**
Deployment (single replica) fires SCHEDULED agents via the django_celery_beat
DatabaseScheduler.

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
        gke_cluster: pulumi.Resource,
        agent_runtime_gsa: gcp.serviceaccount.Account,
        database_url: pulumi.Input[str],
        redis_url: pulumi.Input[str] | None,
        env_secrets: dict[str, pulumi.Input[str]] | None = None,
        kernel_mode: str = "production",
        syscall_pipeline: bool = True,
        replicas: int = 1,
        namespace: str = "forgeos-system",
        cpu: str = "500m",
        memory: str = "2Gi",  # platform boot + MCP uvx install needs headroom
        environment: str = "dev",
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
        # The WI binding references the GKE Workload Identity pool
        # (PROJECT.svc.id.goog), which is auto-created when the GKE cluster
        # with Workload Identity enabled becomes ready. Without an explicit
        # depends_on, Pulumi creates this IAMMember in parallel with the
        # cluster and fails because the pool doesn't exist yet (the cluster
        # takes 5-10+ minutes to provision).
        gcp.serviceaccount.IAMMember(
            f"{name}-wi",
            service_account_id=agent_runtime_gsa.name,
            role="roles/iam.workloadIdentityUser",
            member=pulumi.Output.concat(
                "serviceAccount:", project, ".svc.id.goog[", namespace, "/", ksa_name, "]"
            ),
            opts=pulumi.ResourceOptions(parent=self, depends_on=[gke_cluster]),
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

        labels = {"app": "forgeos-worker", "forgeos.io/system": "worker", "environment": environment}

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
                                # Celery worker tier: the Django web process
                                # enqueues forgeos.run_agent / resume_agent on the
                                # Redis broker; this drains the queues. Each worker
                                # process boots the platform once (worker_process_init)
                                # so FORGEOS_RUNTIME_WORKERS=1 wires the engine/MCP
                                # the inline run + HITL resume use. No HTTP port /
                                # Service — it only consumes the broker.
                                command=["celery", "-A", "forgeos_web.celery_app", "worker"],
                                args=[
                                    "-Q", "agents,agents_resume,scheduled,agents_longrun",
                                    # --concurrency=1: each prefork child boots the
                                    # whole platform + uvx-installs the MCP servers
                                    # in worker_process_init, so N children = N full
                                    # boots → OOM at 1Gi. One child fits (matches the
                                    # prior single-process worker). Scale out with
                                    # replicas, not concurrency.
                                    "--concurrency=1", "--loglevel=info",
                                ],
                                env_from=[
                                    k8s.core.v1.EnvFromSourceArgs(
                                        secret_ref=k8s.core.v1.SecretEnvSourceArgs(
                                            name=env_secret.metadata.name,
                                        ),
                                    )
                                ],
                                env=plain_env,
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

        # Celery Beat — fires SCHEDULED agents (django_celery_beat PeriodicTask)
        # + maintenance ticks. Single replica (do NOT scale >1, or schedules
        # double-fire). Reuses the same image/secret/KSA as the worker.
        beat_labels = {"app": "forgeos-beat", "forgeos.io/system": "beat"}
        self.beat = k8s.apps.v1.Deployment(
            f"{name}-beat",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="forgeos-beat",
                namespace=ns.metadata.name,
                labels=beat_labels,
            ),
            spec=k8s.apps.v1.DeploymentSpecArgs(
                replicas=1,
                selector=k8s.meta.v1.LabelSelectorArgs(match_labels=beat_labels),
                template=k8s.core.v1.PodTemplateSpecArgs(
                    metadata=k8s.meta.v1.ObjectMetaArgs(labels=beat_labels),
                    spec=k8s.core.v1.PodSpecArgs(
                        service_account_name=ksa_name,
                        containers=[
                            k8s.core.v1.ContainerArgs(
                                name="beat",
                                image=image,
                                command=["celery", "-A", "forgeos_web.celery_app", "beat"],
                                args=[
                                    "--scheduler",
                                    "django_celery_beat.schedulers:DatabaseScheduler",
                                    "--loglevel=info",
                                ],
                                env_from=[
                                    k8s.core.v1.EnvFromSourceArgs(
                                        secret_ref=k8s.core.v1.SecretEnvSourceArgs(
                                            name=env_secret.metadata.name,
                                        ),
                                    )
                                ],
                                env=plain_env,
                                resources=k8s.core.v1.ResourceRequirementsArgs(
                                    requests={"cpu": "100m", "memory": "256Mi"},
                                    limits={"cpu": "250m", "memory": "256Mi"},
                                ),
                            )
                        ],
                    ),
                ),
            ),
            opts=pulumi.ResourceOptions(
                parent=self, provider=k8s_provider, depends_on=[ksa, env_secret],
                delete_before_replace=True,
            ),
        )

        self.namespace = ns
        self.register_outputs({})
