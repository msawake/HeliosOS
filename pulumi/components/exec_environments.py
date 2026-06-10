"""Exec-environment sandbox: the `forgeos-envs` namespace + the access the
platform-api needs to drive per-agent sandbox pods.

Background: ForgeOS' EnvironmentManager (src/platform/environments.py) gives a
governed agent a `bash`/`env__exec` tool that the kernel admits (verb
`env.exec`) and then runs *inside a per-agent pod* via `kubectl exec`. The pod
is the sandbox boundary. The app is fully wired for this, but nothing in the
infra ever created the namespace, granted the platform-api access to the
cluster, or constrained the sandbox pods. This component closes that gap.

Security model (least privilege, since the platform-api is internet-facing):
  * GCP IAM: the platform-api GSA gets `roles/container.clusterViewer` — just
    enough to authenticate to the cluster control plane (the GKE IAM gate).
    It deliberately does NOT get `container.developer` (cluster-wide edit).
  * k8s RBAC: a Role in `forgeos-envs` granting exactly the verbs kubectl
    needs to run/exec/teardown sandbox pods, bound to the GSA's email. So the
    platform-api can manage pods *only* in `forgeos-envs`, nowhere else.
  * The sandbox namespace gets a ResourceQuota (caps total sandbox CPU/RAM)
    and a default-deny-ingress NetworkPolicy (nothing may dial into a sandbox).
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp
import pulumi_kubernetes as k8s


class ExecEnvironments(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        project: pulumi.Input[str],
        platform_api_gsa: gcp.serviceaccount.Account,
        k8s_provider: k8s.Provider,
        namespace: str = "forgeos-envs",
        cpu_quota: str = "4",
        memory_quota: str = "8Gi",
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:exec:ExecEnvironments", name, None, opts)
        child = pulumi.ResourceOptions(parent=self, provider=k8s_provider)
        gcp_child = pulumi.ResourceOptions(parent=self)

        # GCP IAM — authenticate to the cluster control plane. clusterViewer is
        # the minimum that lets a principal reach the API server; the actual
        # pod/exec authority comes from the namespaced RBAC below.
        gcp.projects.IAMMember(
            f"{name}-cluster-viewer",
            project=project,
            role="roles/container.clusterViewer",
            member=platform_api_gsa.email.apply(lambda e: f"serviceAccount:{e}"),
            opts=gcp_child,
        )

        # Sandbox namespace.
        self.namespace = k8s.core.v1.Namespace(
            f"{name}-ns",
            metadata=k8s.meta.v1.ObjectMetaArgs(
                name=namespace,
                labels={"forgeos.io/system": "exec-environments"},
            ),
            opts=child,
        )

        # Cap total resources sandbox pods can consume in aggregate.
        k8s.core.v1.ResourceQuota(
            f"{name}-quota",
            metadata=k8s.meta.v1.ObjectMetaArgs(name="forgeos-envs-quota", namespace=namespace),
            spec=k8s.core.v1.ResourceQuotaSpecArgs(
                hard={
                    "requests.cpu": cpu_quota,
                    "requests.memory": memory_quota,
                    "limits.cpu": cpu_quota,
                    "limits.memory": memory_quota,
                },
            ),
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider, depends_on=[self.namespace]),
        )

        # Default-deny ingress — nothing may connect *into* a sandbox pod.
        k8s.networking.v1.NetworkPolicy(
            f"{name}-default-deny",
            metadata=k8s.meta.v1.ObjectMetaArgs(name="default-deny-ingress", namespace=namespace),
            spec=k8s.networking.v1.NetworkPolicySpecArgs(
                pod_selector=k8s.meta.v1.LabelSelectorArgs(),
                policy_types=["Ingress"],
            ),
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider, depends_on=[self.namespace]),
        )

        # Exactly the verbs `kubectl run/exec/wait/delete` need on sandbox pods.
        # (Namespace `get` for _ensure_namespace comes from clusterViewer's
        # cluster-wide read mapping, so it's not needed here.)
        role = k8s.rbac.v1.Role(
            f"{name}-role",
            metadata=k8s.meta.v1.ObjectMetaArgs(name="forgeos-env-exec", namespace=namespace),
            rules=[
                k8s.rbac.v1.PolicyRuleArgs(
                    api_groups=[""],
                    resources=["pods"],
                    verbs=["get", "list", "watch", "create", "delete"],
                ),
                k8s.rbac.v1.PolicyRuleArgs(
                    api_groups=[""],
                    resources=["pods/exec"],
                    verbs=["create", "get"],
                ),
                k8s.rbac.v1.PolicyRuleArgs(
                    api_groups=[""],
                    resources=["pods/log"],
                    verbs=["get"],
                ),
            ],
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider, depends_on=[self.namespace]),
        )

        # Bind the platform-api GSA (as a k8s User = its email) to that Role.
        # GKE maps an authenticated GCP service account to a k8s username equal
        # to its email, so this scopes its pod/exec rights to forgeos-envs only.
        k8s.rbac.v1.RoleBinding(
            f"{name}-rolebinding",
            metadata=k8s.meta.v1.ObjectMetaArgs(name="forgeos-env-exec", namespace=namespace),
            role_ref=k8s.rbac.v1.RoleRefArgs(
                api_group="rbac.authorization.k8s.io",
                kind="Role",
                name=role.metadata.name,
            ),
            subjects=[
                k8s.rbac.v1.SubjectArgs(
                    api_group="rbac.authorization.k8s.io",
                    kind="User",
                    name=platform_api_gsa.email,
                ),
            ],
            opts=pulumi.ResourceOptions(parent=self, provider=k8s_provider, depends_on=[role]),
        )

        self.register_outputs({"namespace": namespace})
