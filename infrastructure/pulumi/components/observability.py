"""Observability — Managed Prometheus PodMonitoring + log sink (optional).

GKE Autopilot enables Cloud Logging and Google Managed Service for Prometheus
by default. This component only adds:
- A PodMonitoring CR per ForgeOS namespace, scraping `/metrics` on agent Pods.

Cloud Trace is consumed automatically when services emit OTLP traces; no infra
resources are required.
"""

from __future__ import annotations

import pulumi
import pulumi_kubernetes as k8s


class Observability(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        forgeos_namespaces: list[str],
        k8s_provider: k8s.Provider,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:observability:Observability", name, None, opts)
        child = pulumi.ResourceOptions(parent=self, provider=k8s_provider)

        for ns_name in forgeos_namespaces:
            k8s.apiextensions.CustomResource(
                f"{name}-{ns_name}-podmon",
                api_version="monitoring.googleapis.com/v1",
                kind="PodMonitoring",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="forgeos-agents",
                    namespace=ns_name,
                ),
                spec={
                    "selector": {
                        "matchExpressions": [
                            {"key": "forgeos.io/agent", "operator": "Exists"}
                        ]
                    },
                    "endpoints": [
                        {"port": "metrics", "interval": "30s", "path": "/metrics"}
                    ],
                },
                opts=child,
            )

        self.register_outputs({})
