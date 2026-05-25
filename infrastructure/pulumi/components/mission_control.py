"""Mission Control on Cloud Run (FastAPI :8888 + Vite SPA bundled as static)."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


class MissionControl(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        image: pulumi.Input[str],
        gsa_email: pulumi.Input[str],
        platform_api_url: pulumi.Input[str],
        mc_admin_password_secret: pulumi.Input[str] | None,
        api_token_secret: pulumi.Input[str] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:mission_control:MissionControl", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        self.service = gcp.cloudrunv2.Service(
            f"{name}-svc",
            name="forgeos-mc",
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
                        ports=gcp.cloudrunv2.ServiceTemplateContainerPortsArgs(
                            container_port=8080,
                        ),
                        envs=_mc_envs(platform_api_url, mc_admin_password_secret, api_token_secret),
                        resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                            limits={"cpu": "1", "memory": "512Mi"},
                            cpu_idle=True,
                        ),
                    )
                ],
            ),
            opts=child,
        )

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


def _mc_envs(
    platform_api_url: pulumi.Input[str],
    mc_admin_password_secret: pulumi.Input[str] | None,
    api_token_secret: pulumi.Input[str] | None,
) -> list:
    envs = [
        gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
            name="FORGEOS_API_URL",
            value=platform_api_url,
        )
    ]
    # MC's config.py reads FORGEOS_MC_PASSWORD (not MC_ADMIN_PASSWORD).
    if mc_admin_password_secret is not None:
        envs.append(
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name="FORGEOS_MC_PASSWORD",
                value_source=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceArgs(
                    secret_key_ref=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                        secret=mc_admin_password_secret,
                        version="latest",
                    )
                ),
            )
        )
    # MC's proxy.py sends `Authorization: Bearer $FORGEOS_API_TOKEN`. Platform
    # API accepts any `Bearer dev-*` token (no value validation), so the value
    # just needs to start with `dev-`.
    if api_token_secret is not None:
        envs.append(
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name="FORGEOS_API_TOKEN",
                value_source=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceArgs(
                    secret_key_ref=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                        secret=api_token_secret,
                        version="latest",
                    )
                ),
            )
        )
    return envs
