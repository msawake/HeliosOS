"""Cloud Run Job that runs `python manage.py migrate` against Cloud SQL.

Distinct from the SQL migrations job (Dockerfile.migrations / psql): that one
applies infrastructure/database/*.sql (the raw schema). THIS one runs the
Django migration graph on the platform-api image — needed for the Django-managed
apps the raw SQL doesn't cover: auth/admin/sessions/contenttypes,
django_celery_beat (Celery Beat's PeriodicTask tables), and the RunPython
migrations (forgeos_rbac, forgeos_rls, forgeos_secrets, forgeos_namespaces).

Models for the existing domain tables are managed=False, so migrate only touches
the app-owned tables; the RunPython DDL migrations are idempotent (DO/EXCEPTION
guards), so re-running is safe. Run it once per deploy (CI / `gcloud run jobs
execute forgeos-django-migrate`), before the worker + beat pods start.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


class DjangoMigrate(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        image: pulumi.Input[str],          # the platform-api image (has Django)
        gsa_email: pulumi.Input[str],      # platform-api GSA (cloudsql.client + db secret)
        database_url_secret: pulumi.Input[str],
        vpc_network: pulumi.Input[str],
        vpc_subnet: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:migrations:DjangoMigrate", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        self.job = gcp.cloudrunv2.Job(
            f"{name}-job",
            name="forgeos-django-migrate",
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
                            # Override the image's default bootstrap CMD.
                            commands=["python", "manage.py", "migrate", "--noinput"],
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
