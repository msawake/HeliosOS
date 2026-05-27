"""Google Service Accounts + Workload Identity Federation bindings.

One GSA per service. Cloud Run services bind directly. GKE workloads bind to
a Kubernetes ServiceAccount in their target namespace via the WI annotation
(written by `namespaces.py` or `agent_base.py`).
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


class Identity(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        project: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:identity:Identity", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        self.project = project

        # One GSA per service
        self.platform_api = gcp.serviceaccount.Account(
            f"{name}-platform-api",
            account_id="forgeos-platform-api",
            display_name="ForgeOS Platform API (Cloud Run)",
            opts=child,
        )
        self.mc = gcp.serviceaccount.Account(
            f"{name}-mc",
            account_id="forgeos-mc",
            display_name="ForgeOS Mission Control (Cloud Run)",
            opts=child,
        )
        self.agent_runtime = gcp.serviceaccount.Account(
            f"{name}-agent",
            account_id="forgeos-agent-runtime",
            display_name="ForgeOS Agent Runtime (GKE pods)",
            opts=child,
        )
        self.migrations = gcp.serviceaccount.Account(
            f"{name}-migrations",
            account_id="forgeos-migrations",
            display_name="ForgeOS DB Migrations (Cloud Run Job)",
            opts=child,
        )

        # Project-level roles
        # Cloud SQL client — needed by platform-api, agents, migrations
        for sa, suffix in [
            (self.platform_api, "platform-api"),
            (self.agent_runtime, "agent"),
            (self.migrations, "migrations"),
        ]:
            gcp.projects.IAMMember(
                f"{name}-{suffix}-sql-client",
                project=project,
                role="roles/cloudsql.client",
                member=sa.email.apply(lambda e: f"serviceAccount:{e}"),
                opts=child,
            )

        # Pub/Sub — platform-api publishes triggers, agent runtime subscribes
        gcp.projects.IAMMember(
            f"{name}-platform-api-pubsub-pub",
            project=project,
            role="roles/pubsub.publisher",
            member=self.platform_api.email.apply(lambda e: f"serviceAccount:{e}"),
            opts=child,
        )
        gcp.projects.IAMMember(
            f"{name}-agent-pubsub-sub",
            project=project,
            role="roles/pubsub.subscriber",
            member=self.agent_runtime.email.apply(lambda e: f"serviceAccount:{e}"),
            opts=child,
        )

        # Artifact Registry — Cloud Run + GKE pull images
        for sa, suffix in [
            (self.platform_api, "platform-api"),
            (self.mc, "mc"),
            (self.agent_runtime, "agent"),
            (self.migrations, "migrations"),
        ]:
            gcp.projects.IAMMember(
                f"{name}-{suffix}-ar-reader",
                project=project,
                role="roles/artifactregistry.reader",
                member=sa.email.apply(lambda e: f"serviceAccount:{e}"),
                opts=child,
            )

        # Logging + monitoring + trace for all services
        for sa, suffix in [
            (self.platform_api, "platform-api"),
            (self.mc, "mc"),
            (self.agent_runtime, "agent"),
            (self.migrations, "migrations"),
        ]:
            for role, slug in (
                ("roles/logging.logWriter", "log-writer"),
                ("roles/monitoring.metricWriter", "metric-writer"),
                ("roles/cloudtrace.agent", "trace-agent"),
            ):
                gcp.projects.IAMMember(
                    f"{name}-{suffix}-{slug}",
                    project=project,
                    role=role,
                    member=sa.email.apply(lambda e: f"serviceAccount:{e}"),
                    opts=child,
                )

        self.register_outputs({})

    def bind_workload_identity(
        self,
        name: str,
        gsa: gcp.serviceaccount.Account,
        k8s_namespace: pulumi.Input[str],
        k8s_sa: str,
        opts: pulumi.ResourceOptions | None = None,
    ) -> gcp.serviceaccount.IAMMember:
        """Allow a KSA to impersonate a GSA via Workload Identity."""
        return gcp.serviceaccount.IAMMember(
            name,
            service_account_id=gsa.name,
            role="roles/iam.workloadIdentityUser",
            member=pulumi.Output.concat(
                "serviceAccount:",
                self.project,
                ".svc.id.goog[",
                k8s_namespace,
                "/",
                k8s_sa,
                "]",
            ),
            opts=opts or pulumi.ResourceOptions(parent=self),
        )
