"""VPC, subnet, Cloud NAT, private services access for Cloud SQL/Memorystore."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


class Network(pulumi.ComponentResource):
    def __init__(
        self,
        name: str,
        region: str,
        network_cidr: str,
        pods_cidr: str,
        services_cidr: str,
        opts: pulumi.ResourceOptions | None = None,
    ) -> None:
        super().__init__("forgeos:network:Network", name, None, opts)
        child = pulumi.ResourceOptions(parent=self)

        self.network = gcp.compute.Network(
            f"{name}-vpc",
            auto_create_subnetworks=False,
            routing_mode="REGIONAL",
            opts=child,
        )

        self.subnet = gcp.compute.Subnetwork(
            f"{name}-subnet",
            ip_cidr_range=network_cidr,
            region=region,
            network=self.network.id,
            private_ip_google_access=True,
            secondary_ip_ranges=[
                gcp.compute.SubnetworkSecondaryIpRangeArgs(
                    range_name="pods",
                    ip_cidr_range=pods_cidr,
                ),
                gcp.compute.SubnetworkSecondaryIpRangeArgs(
                    range_name="services",
                    ip_cidr_range=services_cidr,
                ),
            ],
            opts=child,
        )

        # Cloud NAT — egress to LLM providers and MCP servers
        self.router = gcp.compute.Router(
            f"{name}-router",
            region=region,
            network=self.network.id,
            opts=child,
        )
        self.nat = gcp.compute.RouterNat(
            f"{name}-nat",
            router=self.router.name,
            region=region,
            nat_ip_allocate_option="AUTO_ONLY",
            source_subnetwork_ip_ranges_to_nat="ALL_SUBNETWORKS_ALL_IP_RANGES",
            opts=child,
        )

        # Private services access — for Cloud SQL + Memorystore private IPs
        self.private_ip_range = gcp.compute.GlobalAddress(
            f"{name}-psa-range",
            purpose="VPC_PEERING",
            address_type="INTERNAL",
            prefix_length=16,
            network=self.network.id,
            opts=child,
        )
        self.psa_connection = gcp.servicenetworking.Connection(
            f"{name}-psa-conn",
            network=self.network.id,
            service="servicenetworking.googleapis.com",
            reserved_peering_ranges=[self.private_ip_range.name],
            opts=child,
        )

        # Direct VPC Egress connector (Cloud Run → VPC). Direct VPC Egress doesn't
        # need a connector resource — Cloud Run services reference the subnet
        # directly via `vpc_access.network` + `vpc_access.subnetwork`. Exposed here
        # so callers can wire it up.

        self.register_outputs(
            {
                "network_id": self.network.id,
                "subnet_id": self.subnet.id,
            }
        )
