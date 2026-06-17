"""Google Service Accounts + Workload Identity Federation bindings.

One GSA per service. Cloud Run services bind directly. GKE workloads bind to
a Kubernetes ServiceAccount in their target namespace via the WI annotation
(written by `worker.py` for the durable worker tier).
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
            display_name="Helios OS Platform API (Cloud Run)",
            opts=child,
        )
        self.agent_runtime = gcp.serviceaccount.Account(
            f"{name}-agent",
            account_id="forgeos-agent-runtime",
            display_name="Helios OS Agent Runtime (GKE pods)",
            opts=child,
        )
        self.migrations = gcp.serviceaccount.Account(
            f"{name}-migrations",
            account_id="forgeos-migrations",
            display_name="Helios OS DB Migrations (Cloud Run Job)",
            opts=child,
        )
        self.mcp = gcp.serviceaccount.Account(
            f"{name}-mcp",
            account_id="forgeos-mcp",
            display_name="Helios OS MCP Server (Cloud Run)",
            opts=child,
        )
        self.dashboard = gcp.serviceaccount.Account(
            f"{name}-dashboard",
            account_id="forgeos-dashboard",
            display_name="Helios OS Dashboard (Cloud Run)",
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
            (self.agent_runtime, "agent"),
            (self.migrations, "migrations"),
            (self.mcp, "mcp"),
            (self.dashboard, "dashboard"),
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
            (self.agent_runtime, "agent"),
            (self.migrations, "migrations"),
            (self.mcp, "mcp"),
            (self.dashboard, "dashboard"),
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

        # Per-agent Drive service accounts (treasury demo). Each agent
        # impersonates its OWN SA to access Google Drive — keyless,
        # ``drive.file`` scope — so a user shares a Drive folder with that SA's
        # email to authorize the agent. The platform-api runtime SA gets
        # tokenCreator on each so it can mint impersonated tokens (matching the
        # gcloud-provisioned setup in scripts/provision_agent_sa.sh).
        self.drive_agents: dict[str, gcp.serviceaccount.Account] = {}
        for slug in ("bank-sap", "debt", "po", "mapping", "kyriba"):
            sa = gcp.serviceaccount.Account(
                f"{name}-drive-{slug}",
                account_id=f"drive-agent-{slug}",  # <=30 chars
                display_name=f"Helios OS Drive Agent — {slug}",
                description=f"Per-agent Drive SA for treasury agent {slug}",
                opts=child,
            )
            self.drive_agents[slug] = sa
            gcp.serviceaccount.IAMMember(
                f"{name}-drive-{slug}-tokencreator",
                service_account_id=sa.name,
                role="roles/iam.serviceAccountTokenCreator",
                member=self.platform_api.email.apply(lambda e: f"serviceAccount:{e}"),
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
