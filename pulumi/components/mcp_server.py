"""ForgeOS MCP Server on Cloud Run (FastMCP streamable-http).

Serves src/forgeos_mcp as a remote MCP endpoint so any MCP client (Claude,
Cursor, …) can reach the fleet over HTTP instead of spawning a local stdio
process. It's HTTP-native — it just calls the platform API — so it runs as its
own scale-to-zero Cloud Run service pointed at the platform API URL, reusing
the platform-api image with a CMD override (no separate image/build).

Auth: the server presents FORGEOS_API_KEY to the platform API as X-API-Key,
validated against tenants.api_key_hash (Phase 2). The key is wired only when
its Secret Manager version exists, so a versionless secret doesn't break the
Service (Cloud Run validates secret_key_ref :latest at deploy).
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


class McpServer(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        image: pulumi.Input[str],
        gsa_email: pulumi.Input[str],
        platform_api_url: pulumi.Input[str],
        api_key_secret: pulumi.Input[str] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:mcp_server:McpServer", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        envs = [
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name="FORGEOS_URL",
                value=platform_api_url,
            ),
        ]
        if api_key_secret is not None:
            envs.append(
                gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                    name="FORGEOS_API_KEY",
                    value_source=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceArgs(
                        secret_key_ref=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                            secret=api_key_secret,
                            version="latest",
                        )
                    ),
                )
            )

        self.service = gcp.cloudrunv2.Service(
            f"{name}-svc",
            name="forgeos-mcp",
            location=region,
            ingress="INGRESS_TRAFFIC_ALL",
            template=gcp.cloudrunv2.ServiceTemplateArgs(
                service_account=gsa_email,
                timeout="300s",
                scaling=gcp.cloudrunv2.ServiceTemplateScalingArgs(
                    min_instance_count=0,
                    max_instance_count=5,
                ),
                containers=[
                    gcp.cloudrunv2.ServiceTemplateContainerArgs(
                        image=image,
                        # Reuse the platform-api image; run the MCP server on
                        # streamable-http bound to 0.0.0.0:8080 (Cloud Run port).
                        command=["python", "-m", "src.forgeos_mcp"],
                        args=["--transport", "streamable-http", "--port", "8080", "--host", "0.0.0.0"],
                        ports=gcp.cloudrunv2.ServiceTemplateContainerPortsArgs(
                            container_port=8080,
                        ),
                        envs=envs,
                        resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                            limits={"cpu": "1", "memory": "512Mi"},
                            cpu_idle=True,
                        ),
                    )
                ],
            ),
            opts=child,
        )

        # Public ingress (the platform API behind it enforces auth via the
        # presented API key). Tighten to internal/IAP later if desired.
        gcp.cloudrunv2.ServiceIamMember(
            f"{name}-public",
            location=self.service.location,
            name=self.service.name,
            role="roles/run.invoker",
            member="allUsers",
            opts=child,
        )

        # Clients connect at <url>/mcp (FastMCP streamable-http mount point).
        self.url = self.service.uri
        self.register_outputs({"url": self.url})
