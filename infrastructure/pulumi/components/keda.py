"""KEDA — installed via the official Helm chart in the `keda` namespace."""

from __future__ import annotations

import pulumi
import pulumi_kubernetes as k8s
# Explicit imports work around a Pulumi-runtime lazy-import quirk where
# `k8s.helm.v3.Release` raises AttributeError mid-program even though
# the symbol is importable directly.
from pulumi_kubernetes.helm.v3 import Release as HelmRelease, RepositoryOptsArgs


class Keda(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        k8s_provider: k8s.Provider,
        version: str = "2.15.1",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:keda:Keda", name, None, opts)
        child = pulumi.ResourceOptions(parent=self, provider=k8s_provider)

        self.namespace = k8s.core.v1.Namespace(
            f"{name}-ns",
            metadata=k8s.meta.v1.ObjectMetaArgs(name="keda"),
            opts=child,
        )

        self.release = HelmRelease(
            f"{name}-release",
            chart="keda",
            version=version,
            namespace=self.namespace.metadata.name,
            repository_opts=RepositoryOptsArgs(
                repo="https://kedacore.github.io/charts",
            ),
            opts=child,
        )

        self.register_outputs({})
