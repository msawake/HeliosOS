"""ForgeOS Platform API on Cloud Run (FastAPI :5099).

Direct VPC Egress to reach Cloud SQL (private IP), Memorystore, and agent Pods.
Secrets mounted as env from Secret Manager. Public ingress on *.run.app URL.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


class PlatformApi(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        image: pulumi.Input[str],
        gsa_email: pulumi.Input[str],
        vpc_network: pulumi.Input[str],
        vpc_subnet: pulumi.Input[str],
        secret_refs: dict[str, pulumi.Input[str]],
        pubsub_topic: pulumi.Input[str],
        extra_env: dict[str, pulumi.Input[str]] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:platform_api:PlatformApi", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        envs = [
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name=k,
                value_source=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceArgs(
                    secret_key_ref=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                        secret=v,
                        version="latest",
                    )
                ),
            )
            for k, v in secret_refs.items()
        ]
        envs.append(
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name="FORGEOS_PUBSUB_TOPIC",
                value=pubsub_topic,
            )
        )
        envs.append(
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name="FORGEOS_SYSCALL_PIPELINE",
                value="1",
            )
        )
        # Runtime/kernel flags (FORGEOS_RUNTIME_V2/WORKERS, FORGEOS_KERNEL_MODE,
        # GCP_PROJECT_ID, …). platform-api enqueues to the shared Redis worker
        # tier and also processes opportunistically while an instance is warm;
        # the always-on GKE worker (WorkerTier) guarantees draining/resume.
        for _k, _v in (extra_env or {}).items():
            envs.append(gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(name=_k, value=_v))

        self.service = gcp.cloudrunv2.Service(
            f"{name}-svc",
            name="forgeos-platform-api",
            location=region,
            ingress="INGRESS_TRAFFIC_ALL",
            template=gcp.cloudrunv2.ServiceTemplateArgs(
                service_account=gsa_email,
                timeout="300s",
                scaling=gcp.cloudrunv2.ServiceTemplateScalingArgs(
                    min_instance_count=0,
                    max_instance_count=10,
                ),
                vpc_access=gcp.cloudrunv2.ServiceTemplateVpcAccessArgs(
                    network_interfaces=[
                        gcp.cloudrunv2.ServiceTemplateVpcAccessNetworkInterfaceArgs(
                            network=vpc_network,
                            subnetwork=vpc_subnet,
                        )
                    ],
                    egress="ALL_TRAFFIC",
                ),
                containers=[
                    gcp.cloudrunv2.ServiceTemplateContainerArgs(
                        image=image,
                        ports=gcp.cloudrunv2.ServiceTemplateContainerPortsArgs(
                            container_port=5000,
                        ),
                        envs=envs,
                        resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                            limits={"cpu": "2", "memory": "2Gi"},
                            cpu_idle=True,
                        ),
                    )
                ],
            ),
            opts=child,
        )

        # Public unauthenticated access (Mission Control handles its own auth gate;
        # the platform API itself uses API key headers checked in code).
        gcp.cloudrunv2.ServiceIamMember(
            f"{name}-public",
            location=self.service.location,
            name=self.service.name,
            role="roles/run.invoker",
            member="allUsers",
            opts=child,
        )

        self.url = self.service.uri
        self.register_outputs({"url": self.url})
