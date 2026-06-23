"""Artifact Registry — creates and manages the Docker repo for Helios OS images.

Pulumi owns the repo lifecycle so the full stack is self-contained: a fresh
`pulumi up` on a new project creates the repo, builds can push immediately,
and `pulumi destroy` tears it down cleanly.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


class Registry(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        project: str,
        repo_id: str = "forgeos",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:registry:Registry", name, None, opts)

        self.repo = gcp.artifactregistry.Repository(
            f"{name}-repo",
            repository_id=repo_id,
            location=region,
            format="DOCKER",
            description="Helios OS container images",
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.url = pulumi.Output.concat(
            region, "-docker.pkg.dev/", project, "/", repo_id
        )

        self.register_outputs({"repo_url": self.url})
