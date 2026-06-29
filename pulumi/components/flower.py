"""Flower — Celery monitoring UI for the worker tier.

Runs the upstream ``mher/flower:2`` image as a single-replica Deployment in
the same ``forgeos-system`` namespace as the workers, pointed at the same
Memorystore Redis broker. Exposed via a ClusterIP Service on :5555 —
``kubectl -n forgeos-system port-forward svc/forgeos-flower 5555:5555`` and
open http://localhost:5555.

No public ingress; no extra IAM. The image bundles Flower 2.x and python; we
only pass it ``--broker=$BROKER_URL`` via the same ``forgeos-worker-env``
k8s Secret the workers already mount, so REDIS_URL stays the one source of
truth.
"""

from __future__ import annotations

import pulumi
import pulumi_kubernetes as k8s


class Flower(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        k8s_provider: k8s.Provider,
        namespace: pulumi.Input[str],
        env_secret_name: pulumi.Input[str],
        gke_cluster: pulumi.Resource | None = None,
        image: str = "mher/flower:2.0",
        replicas: int = 1,
        environment: str = "dev",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:flower:Flower", name, None, opts)
        deps = [gke_cluster] if gke_cluster is not None else []
        child = pulumi.ResourceOptions(parent=self, provider=k8s_provider, depends_on=deps)

        labels = {
            "app": "forgeos-flower",
            "forgeos.io/system": "flower",
            "environment": environment,
        }

        self.deployment = k8s.apps.v1.Deployment(
            f"{name}-deploy",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="forgeos-flower",
                namespace=namespace,
                labels=labels,
                # Don't block pulumi up on pod readiness — the broker may be
                # warming up; pods will retry per kubelet backoff.
                annotations={"pulumi.com/skipAwait": "true"},
            ),
            spec=k8s.apps.v1.DeploymentSpecArgs(
                replicas=replicas,
                selector=k8s.meta.v1.LabelSelectorArgs(match_labels={"app": "forgeos-flower"}),
                template=k8s.core.v1.PodTemplateSpecArgs(
                    metadata=k8s.meta.v1.ObjectMetaArgs(labels=labels),
                    spec=k8s.core.v1.PodSpecArgs(
                        containers=[
                            k8s.core.v1.ContainerArgs(
                                name="flower",
                                image=image,
                                # Flower reads --broker from its CLI. We can't
                                # interpolate env into argv directly, so let
                                # the container do it via sh -c.
                                command=["sh", "-c"],
                                args=[
                                    "exec celery --broker=$REDIS_URL "
                                    "flower --address=0.0.0.0 --port=5555 "
                                    "--persistent=False --max_tasks=10000"
                                ],
                                env_from=[
                                    k8s.core.v1.EnvFromSourceArgs(
                                        secret_ref=k8s.core.v1.SecretEnvSourceArgs(
                                            name=env_secret_name,
                                        ),
                                    ),
                                ],
                                ports=[k8s.core.v1.ContainerPortArgs(container_port=5555)],
                                resources=k8s.core.v1.ResourceRequirementsArgs(
                                    requests={"cpu": "100m", "memory": "256Mi"},
                                    limits={"cpu": "500m", "memory": "512Mi"},
                                ),
                                readiness_probe=k8s.core.v1.ProbeArgs(
                                    http_get=k8s.core.v1.HTTPGetActionArgs(
                                        path="/metrics", port=5555,
                                    ),
                                    initial_delay_seconds=10,
                                    period_seconds=15,
                                ),
                            ),
                        ],
                    ),
                ),
            ),
            opts=child,
        )

        self.service = k8s.core.v1.Service(
            f"{name}-svc",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name="forgeos-flower",
                namespace=namespace,
                labels=labels,
            ),
            spec=k8s.core.v1.ServiceSpecArgs(
                type="ClusterIP",
                selector={"app": "forgeos-flower"},
                ports=[k8s.core.v1.ServicePortArgs(
                    name="http", port=5555, target_port=5555, protocol="TCP",
                )],
            ),
            opts=child,
        )

        self.register_outputs({
            "deployment_name": self.deployment.metadata.name,
            "service_name": self.service.metadata.name,
        })
