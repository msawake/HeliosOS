"""Cloud Run Job that runs infrastructure/database/*.sql against Cloud SQL.

The job image is expected at `<registry>/migrations:<tag>` — built from a
small Dockerfile that bundles psql + the SQL files and runs them in order.

Pulumi creates the Job resource and executes it once per deploy by setting
`launch_stage` and triggering via the GCP SDK in a follow-up automation
(or `gcloud run jobs execute forgeos-migrations` from CI). We don't auto-run
on every `pulumi up` because that would replay migrations on no-op deploys.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


class Migrations(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        image: pulumi.Input[str],
        gsa_email: pulumi.Input[str],
        database_url_secret: pulumi.Input[str],
        vpc_network: pulumi.Input[str],
        vpc_subnet: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:migrations:Migrations", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        self.job = gcp.cloudrunv2.Job(
            f"{name}-job",
            name="forgeos-migrations",
            location=region,
            template=gcp.cloudrunv2.JobTemplateArgs(
                template=gcp.cloudrunv2.JobTemplateTemplateArgs(
                    service_account=gsa_email,
                    timeout="600s",
                    max_retries=1,
                    vpc_access=gcp.cloudrunv2.JobTemplateTemplateVpcAccessArgs(
                        network_interfaces=[
                            gcp.cloudrunv2.JobTemplateTemplateVpcAccessNetworkInterfaceArgs(
                                network=vpc_network,
                                subnetwork=vpc_subnet,
                            )
                        ],
                        egress="ALL_TRAFFIC",
                    ),
                    containers=[
                        gcp.cloudrunv2.JobTemplateTemplateContainerArgs(
                            image=image,
                            envs=[
                                gcp.cloudrunv2.JobTemplateTemplateContainerEnvArgs(
                                    name="DATABASE_URL",
                                    value_source=gcp.cloudrunv2.JobTemplateTemplateContainerEnvValueSourceArgs(
                                        secret_key_ref=gcp.cloudrunv2.JobTemplateTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                            secret=database_url_secret,
                                            version="latest",
                                        )
                                    ),
                                ),
                            ],
                            resources=gcp.cloudrunv2.JobTemplateTemplateContainerResourcesArgs(
                                limits={"cpu": "1", "memory": "512Mi"},
                            ),
                        )
                    ],
                )
            ),
            opts=child,
        )

        self.register_outputs({"job_name": self.job.name})
