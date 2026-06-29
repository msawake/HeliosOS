"""Project-level admin IAM grants.

Grants `roles/owner` to a configurable list of principals on the GCP project.
Created so the `toolshub.admin@group.makingscience.com` Google Group (and any
other principals an operator drops into `forgeos-gcp:admin_members`) has the
access an admin actually needs: read secret values, run/inspect Cloud Run
jobs, see logs, manage Cloud SQL, grant further IAM. Without this, only the
WIF deploy SA can act on these projects (see no-IAM-on-remote-envs).

Default member: the `toolshub.admin` Google Group. To add more, set:

    pulumi config set --path 'admin_members[1]' user:alice@makingscience.com

Each principal must include the IAM prefix (`group:`, `user:`,
`serviceAccount:`, `domain:`). `IAMMember` is non-authoritative — adds to
existing bindings rather than replacing them — so it is safe to run.
"""

from __future__ import annotations

import re

import pulumi
import pulumi_gcp as gcp


_DEFAULT_MEMBERS: tuple[str, ...] = (
    "group:toolshub.admin@group.makingscience.com",
    # Direct user binding as a backstop for operators not yet in the group above
    # (Google Group membership is the long-term path; remove this once Antoni
    # Bergas is added to toolshub.admin).
    "user:antoni.bergas@makingscience.com",
)

_PREFIX_RE = re.compile(r"^(user|group|serviceAccount|domain|principal|principalSet):.+")


class Admin(pulumi.ComponentResource):
    """Grants roles/owner on the project to each configured principal."""

    def __init__(
        self,
        name: str,
        project: pulumi.Input[str],
        members: list[str] | None = None,
        role: str = "roles/owner",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:iam:Admin", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        resolved = list(members) if members else list(_DEFAULT_MEMBERS)
        for m in resolved:
            if not _PREFIX_RE.match(m):
                raise ValueError(
                    f"admin member {m!r} must be prefixed (group:/user:/serviceAccount:/...)"
                )

        self.bindings: list[gcp.projects.IAMMember] = []
        for m in resolved:
            # Stable, member-derived slug so re-ordering the list doesn't churn
            # Pulumi state. The IAM member name itself is descriptive enough.
            slug = re.sub(r"[^a-zA-Z0-9-]+", "-", m).strip("-").lower()
            self.bindings.append(
                gcp.projects.IAMMember(
                    f"{name}-{slug}",
                    project=project,
                    role=role,
                    member=m,
                    opts=child,
                )
            )

        self.register_outputs({"members": resolved, "role": role})
