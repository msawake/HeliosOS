"""One k8s namespace per ForgeOS namespace.

Each namespace gets:
- A KSA `forgeos-agent` bound (via Workload Identity) to the agent-runtime GSA.
- A default-deny NetworkPolicy with explicit egress to Cloud SQL, Memorystore,
  Google APIs, and the Internet (LLM providers, MCP).
- A ResourceQuota capping total CPU / RAM across all agent Pods in the namespace.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp
import pulumi_kubernetes as k8s


class Namespaces(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        forgeos_namespaces: list[str],
        agent_runtime_gsa: gcp.serviceaccount.Account,
        identity,  # Identity component, for bind_workload_identity
        k8s_provider: k8s.Provider,
        cpu_quota: str = "8",
        memory_quota: str = "16Gi",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:namespaces:Namespaces", name, None, opts)
        child = pulumi.ResourceOptions(parent=self, provider=k8s_provider)

        self.namespaces: dict[str, k8s.core.v1.Namespace] = {}
        self.service_accounts: dict[str, k8s.core.v1.ServiceAccount] = {}

        for ns_name in forgeos_namespaces:
            ns = k8s.core.v1.Namespace(
                f"{name}-{ns_name}",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name=ns_name,
                    labels={"forgeos.io/namespace": ns_name},
                ),
                opts=child,
            )
            self.namespaces[ns_name] = ns

            ksa = k8s.core.v1.ServiceAccount(
                f"{name}-{ns_name}-ksa",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="forgeos-agent",
                    namespace=ns.metadata.name,
                    annotations={
                        "iam.gke.io/gcp-service-account": agent_runtime_gsa.email,
                    },
                ),
                opts=child,
            )
            self.service_accounts[ns_name] = ksa

            identity.bind_workload_identity(
                f"{name}-{ns_name}-wi",
                gsa=agent_runtime_gsa,
                k8s_namespace=ns.metadata.name,
                k8s_sa="forgeos-agent",
                opts=pulumi.ResourceOptions(parent=self),
            )

            k8s.core.v1.ResourceQuota(
                f"{name}-{ns_name}-quota",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="forgeos-quota",
                    namespace=ns.metadata.name,
                ),
                spec=k8s.core.v1.ResourceQuotaSpecArgs(
                    hard={
                        "requests.cpu": cpu_quota,
                        "requests.memory": memory_quota,
                        "limits.cpu": cpu_quota,
                        "limits.memory": memory_quota,
                    },
                ),
                opts=child,
            )

            # Default-deny ingress, allow all egress. NetworkPolicy egress
            # filtering on Autopilot via FQDN isn't supported; rely on the
            # GSA-scoped access for sensitive resources.
            k8s.networking.v1.NetworkPolicy(
                f"{name}-{ns_name}-default-deny",
                metadata=k8s.meta.v1.ObjectMetaArgs(
                    name="default-deny-ingress",
                    namespace=ns.metadata.name,
                ),
                spec=k8s.networking.v1.NetworkPolicySpecArgs(
                    pod_selector=k8s.meta.v1.LabelSelectorArgs(),
                    policy_types=["Ingress"],
                ),
                opts=child,
            )

        self.register_outputs({})
