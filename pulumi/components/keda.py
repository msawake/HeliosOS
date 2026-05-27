"""KEDA — installed via the official Helm chart in the `keda` namespace."""

from __future__ import annotations

import pulumi
import pulumi_kubernetes as k8s


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

        self.release = k8s.helm.v3.Release(
            f"{name}-release",
            chart="keda",
            version=version,
            namespace=self.namespace.metadata.name,
            repository_opts=k8s.helm.v3.RepositoryOptsArgs(
                repo="https://kedacore.github.io/charts",
            ),
            opts=child,
        )

        self.register_outputs({})
