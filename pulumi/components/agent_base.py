"""Per-agent workload: Deployment + KEDA ScaledObject + Pub/Sub subscription.

Instantiate once per deployed agent:

    AgentWorkload(
        name="lead-qualifier",
        namespace="sales-team",
        image="europe-west1-docker.pkg.dev/proj/forgeos/agent-base:v1",
        manifest_ref="gs://…/manifests/lead-qualifier.yaml",
        pubsub_topic=pubsub_topic_name,
        cpu="250m", memory="512Mi",
    )

Defaults match the agreed sizing (250m / 512Mi). Manifests can request more by
passing different `cpu`/`memory` values.

Scaling: KEDA ScaledObject watches a per-agent Pub/Sub subscription, scaling
the Deployment between `min_replicas` (0 for scheduled/event/reflex; 1 for
always_on) and `max_replicas` based on un-acked message count.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp
import pulumi_kubernetes as k8s


class AgentWorkload(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        namespace: pulumi.Input[str],
        image: pulumi.Input[str],
        manifest_ref: pulumi.Input[str],
        pubsub_topic: pulumi.Input[str],
        project: pulumi.Input[str],
        k8s_provider: k8s.Provider,
        platform_api_url: pulumi.Input[str],
        secret_refs: dict[str, pulumi.Input[str]] | None = None,
        cpu: str = "250m",
        memory: str = "512Mi",
        always_on: bool = True,
        max_replicas: int = 10,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:agent:AgentWorkload", name, None, opts)
        child = pulumi.ResourceOptions(parent=self, provider=k8s_provider)
        gcp_child = pulumi.ResourceOptions(parent=self)

        labels = {"app": name, "forgeos.io/agent": name}

        # Per-agent Pub/Sub subscription (KEDA scales on its backlog)
        subscription = gcp.pubsub.Subscription(
            f"{name}-sub",
            topic=pubsub_topic,
            ack_deadline_seconds=60,
            filter=f'attributes.agent = "{name}"',
            opts=gcp_child,
        )

        env = [
            k8s.core.v1.EnvVarArgs(name="FORGEOS_AGENT_NAME", value=name),
            k8s.core.v1.EnvVarArgs(name="FORGEOS_AGENT_MANIFEST", value=manifest_ref),
            k8s.core.v1.EnvVarArgs(name="FORGEOS_API_URL", value=platform_api_url),
            k8s.core.v1.EnvVarArgs(name="FORGEOS_PUBSUB_SUBSCRIPTION", value=subscription.name),
            k8s.core.v1.EnvVarArgs(name="FORGEOS_SYSCALL_PIPELINE", value="1"),
        ]
        # Secrets injected via CSI driver projected as files; or, for simplicity here,
        # passed as references for the agent runtime to fetch via Secret Manager client
        # using its bound GSA.
        for k, v in (secret_refs or {}).items():
            env.append(k8s.core.v1.EnvVarArgs(name=f"{k}_SECRET", value=v))

        deployment = k8s.apps.v1.Deployment(
            f"{name}-deploy",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=name,
                namespace=namespace,
                labels=labels,
            ),
            spec=k8s.apps.v1.DeploymentSpecArgs(
                replicas=1 if always_on else 0,
                selector=k8s.meta.v1.LabelSelectorArgs(match_labels=labels),
                template=k8s.core.v1.PodTemplateSpecArgs(
                    metadata=k8s.meta.v1.ObjectMetaArgs(labels=labels),
                    spec=k8s.core.v1.PodSpecArgs(
                        service_account_name="forgeos-agent",
                        containers=[
                            k8s.core.v1.ContainerArgs(
                                name="agent",
                                image=image,
                                env=env,
                                ports=[
                                    k8s.core.v1.ContainerPortArgs(
                                        name="metrics",
                                        container_port=9100,
                                    )
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
            opts=child,
        )

        # KEDA ScaledObject — scales from 0..max_replicas on Pub/Sub backlog
        scaledobject = k8s.apiextensions.CustomResource(
            f"{name}-scaler",
            api_version="keda.sh/v1alpha1",
            kind="ScaledObject",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=f"{name}-scaler",
                namespace=namespace,
            ),
            spec={
                "scaleTargetRef": {"name": name},
                "minReplicaCount": 1 if always_on else 0,
                "maxReplicaCount": max_replicas,
                "pollingInterval": 15,
                "cooldownPeriod": 120,
                "triggers": [
                    {
                        "type": "gcp-pubsub",
                        "metadata": {
                            "subscriptionName": subscription.name,
                            "subscriptionSize": "5",
                            "mode": "SubscriptionSize",
                        },
                    }
                ],
            },
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider, depends_on=[deployment]),
        )

        self.deployment = deployment
        self.subscription = subscription
        self.scaledobject = scaledobject
        self.register_outputs({})
