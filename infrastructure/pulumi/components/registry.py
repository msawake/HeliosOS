"""Artifact Registry — references an existing Docker repo.

The `forgeos` repo is provisioned out-of-band (via `gcloud artifacts
repositories create`) so that container images can be built and pushed
before the first `pulumi up`. Pulumi reads its URL via .get() and never
manages the repo lifecycle.
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

        # Reference an already-existing repository — Pulumi does not own it.
        self.repo = gcp.artifactregistry.Repository.get(
            f"{name}-repo",
            id=f"projects/{project}/locations/{region}/repositories/{repo_id}",
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.url = f"{region}-docker.pkg.dev/{project}/{repo_id}"

        self.register_outputs({"repo_url": self.url})
