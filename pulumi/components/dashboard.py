"""Helios OS dashboard (Next.js) on Cloud Run.

The web UI — a pure HTTP client of the platform API. It holds no state and
needs no DB/secret access; it just proxies the browser to the platform API via
`FORGEOS_API_URL` (next.config rewrites `/api/*` there). Runs as its own
scale-to-zero Cloud Run service on the prebuilt `forgeos-dashboard` image.

`deletion_protection=False` is set explicitly so the service can be torn down
with `pulumi destroy` without the client-side guard that blocks cloudrunv2
deletes by default.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


class Dashboard(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        image: pulumi.Input[str],
        gsa_email: pulumi.Input[str],
        platform_api_url: pulumi.Input[str],
        environment: str = "dev",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:dashboard:Dashboard", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        self.service = gcp.cloudrunv2.Service(
            f"{name}-svc",
            name="forgeos-dashboard",
            location=region,
            ingress="INGRESS_TRAFFIC_ALL",
            deletion_protection=False,
            labels={"environment": environment, "component": "dashboard"},
            template=gcp.cloudrunv2.ServiceTemplateArgs(
                labels={"environment": environment, "component": "dashboard"},
                service_account=gsa_email,
                timeout="300s",
                scaling=gcp.cloudrunv2.ServiceTemplateScalingArgs(
                    min_instance_count=0,
                    max_instance_count=5,
                ),
                containers=[
                    gcp.cloudrunv2.ServiceTemplateContainerArgs(
                        image=image,
                        ports=gcp.cloudrunv2.ServiceTemplateContainerPortsArgs(
                            container_port=3000,
                        ),
                        envs=[
                            # Browser /api/* + SSR calls are rewritten here by
                            # next.config — point them at the platform API.
                            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                                name="FORGEOS_API_URL",
                                value=platform_api_url,
                            ),
                            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                                name="NODE_ENV",
                                value="production",
                            ),
                        ],
                        resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                            limits={"cpu": "1", "memory": "512Mi"},
                            cpu_idle=True,
                        ),
                    )
                ],
            ),
            opts=child,
        )

        # Public ingress — it's a browser-facing UI.
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
