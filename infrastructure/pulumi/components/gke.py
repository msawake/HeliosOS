"""GKE Autopilot cluster + Kubernetes Provider."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp
import pulumi_kubernetes as k8s


class Gke(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        network_id: pulumi.Input[str],
        subnet_id: pulumi.Input[str],
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:gke:Gke", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        self.cluster = gcp.container.Cluster(
            f"{name}-autopilot",
            location=region,
            enable_autopilot=True,
            network=network_id,
            subnetwork=subnet_id,
            ip_allocation_policy=gcp.container.ClusterIpAllocationPolicyArgs(
                cluster_secondary_range_name="pods",
                services_secondary_range_name="services",
            ),
            private_cluster_config=gcp.container.ClusterPrivateClusterConfigArgs(
                enable_private_nodes=True,
                enable_private_endpoint=False,
                master_ipv4_cidr_block="172.16.0.0/28",
            ),
            release_channel=gcp.container.ClusterReleaseChannelArgs(channel="REGULAR"),
            deletion_protection=False,
            opts=child,
        )

        # kubeconfig synthesized from cluster outputs (no gcloud dependency at runtime).
        self.kubeconfig = pulumi.Output.all(
            name=self.cluster.name,
            endpoint=self.cluster.endpoint,
            ca=self.cluster.master_auth.cluster_ca_certificate,
        ).apply(_render_kubeconfig)

        self.provider = k8s.Provider(
            f"{name}-k8s",
            kubeconfig=self.kubeconfig,
            opts=child,
        )

        self.register_outputs({"cluster_name": self.cluster.name})


def _render_kubeconfig(args: dict) -> str:
    name = args["name"]
    endpoint = args["endpoint"]
    ca = args["ca"]
    return f"""apiVersion: v1
kind: Config
clusters:
- name: {name}
  cluster:
    server: https://{endpoint}
    certificate-authority-data: {ca}
contexts:
- name: {name}
  context:
    cluster: {name}
    user: {name}
current-context: {name}
users:
- name: {name}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: gke-gcloud-auth-plugin
      installHint: Install gke-gcloud-auth-plugin via gcloud components install gke-gcloud-auth-plugin
      provideClusterInfo: true
"""
