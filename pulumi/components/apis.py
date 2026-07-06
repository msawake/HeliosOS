"""Ensure required GCP project APIs are enabled.

Belt-and-suspenders: Terraform owns API enablement (ms_tool_hub_iac); this
only ensures a fresh project can converge. disable_on_destroy=False avoids
ownership conflicts.
"""

from __future__ import annotations

from typing import Sequence

import pulumi
import pulumi_gcp as gcp

# Canonical API list — kept in sync with helios-dev.yaml active-apis block.
DEFAULT_APIS: list[str] = [
    # Core Infrastructure
    "compute.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "servicenetworking.googleapis.com",
    # Container Services
    "container.googleapis.com",
    "artifactregistry.googleapis.com",
    # Runtime
    "run.googleapis.com",
    # Data Services
    "sqladmin.googleapis.com",
    "redis.googleapis.com",
    "pubsub.googleapis.com",
    # Secrets
    "secretmanager.googleapis.com",
    # Monitoring & Logging
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "cloudtrace.googleapis.com",
    # Build & Storage
    "cloudbuild.googleapis.com",
    "storage.googleapis.com",
    # KMS (Pulumi secrets encryption)
    "cloudkms.googleapis.com",
    # IAM & Auth (WIF + SA token generation)
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
]


class EnabledApis(pulumi.ComponentResource):
    """Enable a set of GCP service APIs on a project.

    Each API becomes a ``gcp.projects.Service`` child resource with
    ``disable_on_destroy=False`` so Terraform retains ownership.
    """

    def __init__(
        self,
        name: str,
        project: str,
        services: Sequence[str] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:apis:EnabledApis", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        for svc in services if services is not None else DEFAULT_APIS:
            # Deterministic child name: use the first dot-segment as a short label.
            label = svc.split(".")[0]
            gcp.projects.Service(
                f"{name}-{label}",
                project=project,
                service=svc,
                disable_on_destroy=False,
                opts=child,
            )

        self.register_outputs({})
