# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: Apache-2.0
"""
Run the Helios OS MCP Server.

    python3 -m src.forgeos_mcp                              # stdio
    python3 -m src.forgeos_mcp --transport sse              # SSE
    python3 -m src.forgeos_mcp --transport streamable-http  # HTTP
"""

import argparse

from src.forgeos_mcp.server import server


def main():
    parser = argparse.ArgumentParser(description="Helios OS MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for SSE/HTTP transport")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind host for SSE/HTTP transport (0.0.0.0 for containers/Cloud Run)",
    )
    args = parser.parse_args()

    if args.transport != "stdio":
        server.settings.port = args.port
        server.settings.host = args.host
        # Behind Cloud Run / a load balancer the inbound Host header is the
        # service domain, not localhost — FastMCP's DNS-rebinding guard would
        # reject it with HTTP 421. The platform API we proxy to enforces real
        # auth, so disable the guard for HTTP transports.
        from mcp.server.transport_security import TransportSecuritySettings
        server.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )

    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
