"""
MCP Server lifecycle manager.

Reads MCP server configurations from company config YAML,
connects to each server, discovers available tools via list_tools(),
and provides connected clients to the ToolExecutor.

Gracefully degrades when the `mcp` package is not installed or
when servers fail to connect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Try to import the MCP SDK
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    logger.info("mcp SDK not installed — MCP servers will not be connected")


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""
    name: str
    package: str
    required: bool = False
    tier: int = 1
    env_vars: dict[str, str] = field(default_factory=dict)
    args: list[str] = field(default_factory=list)


class MCPServerManager:
    """
    Manages MCP server connections and tool discovery.

    Reads config, connects to servers, calls list_tools() on each,
    and provides clients + schemas to the rest of the system.
    """

    def __init__(self, config: dict | None = None, secrets_manager: Any | None = None):
        self._server_configs = self._parse_config(config or {})
        self._clients: dict[str, Any] = {}
        self._tool_schemas: dict[str, list[dict]] = {}
        self._sessions: list[Any] = []
        self._secrets_manager = secrets_manager

    def _parse_config(self, config: dict) -> list[MCPServerConfig]:
        """Parse mcp_servers section from company config YAML."""
        mcp_section = config.get("mcp_servers", {})
        servers: list[MCPServerConfig] = []

        for tier_key in ("tier1", "tier2", "tier3"):
            tier_num = int(tier_key[-1])
            tier_list = mcp_section.get(tier_key, [])
            for entry in tier_list:
                if isinstance(entry, dict):
                    servers.append(MCPServerConfig(
                        name=entry.get("name", ""),
                        package=entry.get("package", ""),
                        required=entry.get("required", False),
                        tier=tier_num,
                        env_vars=entry.get("env_vars", {}),
                        args=entry.get("args", []),
                    ))

        return servers

    async def connect_all(self) -> dict[str, Any]:
        """Connect to all configured MCP servers.

        Returns a dict of {server_name: client} for use by ToolExecutor.
        Gracefully handles missing SDK and connection failures.
        """
        if not HAS_MCP:
            if self._server_configs:
                logger.warning(
                    "MCP SDK not installed — %d servers configured but cannot connect. "
                    "Install with: pip install mcp",
                    len(self._server_configs),
                )
            return {}

        async def _connect_and_discover(server_config: MCPServerConfig):
            try:
                client = await self._connect_server(server_config)
                if client:
                    self._clients[server_config.name] = client

                    # Discover tools
                    tools = await client.list_tools()
                    self._tool_schemas[server_config.name] = [
                        {
                            "name": tool.name,
                            "description": getattr(tool, "description", ""),
                            "inputSchema": getattr(tool, "inputSchema", {}),
                        }
                        for tool in tools.tools
                    ]
                    logger.info(
                        "MCP connected: %s (%d tools discovered)",
                        server_config.name, len(tools.tools),
                    )
            except Exception as e:
                if server_config.required:
                    logger.error(
                        "Required MCP server '%s' failed to connect: %s",
                        server_config.name, e,
                    )
                else:
                    logger.warning(
                        "Optional MCP server '%s' failed to connect: %s",
                        server_config.name, e,
                    )

        import asyncio
        tasks = [_connect_and_discover(config) for config in self._server_configs]
        await asyncio.gather(*tasks)

        connected = len(self._clients)
        total = len(self._server_configs)
        logger.info("MCP servers: %d/%d connected", connected, total)

        return dict(self._clients)

    async def connect_one(
        self,
        name: str,
        package: str,
        env_vars: dict[str, str] | None = None,
        args: list[str] | None = None,
    ) -> list[dict]:
        """Connect a single MCP server on demand and discover its tools.

        Used to bring up a server registered at runtime (e.g. from the
        dashboard) without a full reboot. Returns the discovered tool
        schemas (same shape as ``get_all_tool_schemas()`` values) so the
        caller can register them with the ToolExecutor. Re-registering an
        existing server reconnects it. Raises on connection failure.
        """
        if not HAS_MCP:
            raise RuntimeError("MCP SDK not installed — cannot connect server")
        cfg = MCPServerConfig(
            name=name,
            package=package,
            env_vars=env_vars or {},
            args=args or [],
        )
        client = await self._connect_server(cfg)
        if not client:
            raise RuntimeError(f"MCP server '{name}' did not return a client")
        self._clients[name] = client
        tools = await client.list_tools()
        schemas = [
            {
                "name": tool.name,
                "description": getattr(tool, "description", ""),
                "inputSchema": getattr(tool, "inputSchema", {}),
            }
            for tool in tools.tools
        ]
        self._tool_schemas[name] = schemas
        logger.info("MCP connected on demand: %s (%d tools discovered)", name, len(schemas))
        return schemas

    async def _connect_server(self, config: MCPServerConfig) -> Any | None:
        """Connect to a single MCP server via stdio transport."""
        if not HAS_MCP:
            return None

        # Determine command based on package type
        package = config.package
        if package.startswith("@") or package.startswith("mcp-server-"):
            # npm package — run via npx
            command = "npx"
            args = ["-y", package] + config.args
        else:
            # Python package — run via python -m or uvx
            command = "uvx"
            args = [package] + config.args

        # Resolve environment variables securely at runtime. We always start
        # from a copy of os.environ so the child inherits PATH, HOME, TMPDIR,
        # PYTHONHOME, etc. (the mcp SDK passes `env` straight to Popen — when
        # set to a bare dict it REPLACES the parent env, which makes some MCP
        # servers come up but expose zero tools because dependent files can't
        # be found).
        import os as _os
        resolved_env = _os.environ.copy()
        if config.env_vars:
            cred_store = None
            if self._secrets_manager:
                from src.platform.credentials import CredentialStore, SCOPE_PLATFORM
                cred_store = CredentialStore(self._secrets_manager)
            for k, v in config.env_vars.items():
                if v.startswith("secret:"):
                    # Extract secret name (e.g., "secret:github-token" -> "github-token")
                    secret_name = v[7:]
                    if cred_store is not None:
                        # Boot-time servers are company-wide: resolve platform
                        # scope, then literal/legacy names (+ env) as fallback.
                        resolved_val = cred_store.resolve(
                            secret_name,
                            namespace=None,
                            user_id="default",
                            order=(SCOPE_PLATFORM,),
                            caller=f"mcp_server_{config.name}",
                        )
                        if resolved_val:
                            resolved_env[k] = resolved_val
                        else:
                            logger.warning(f"Secret '{secret_name}' not found for MCP server '{config.name}'")
                    else:
                        # Fallback to os.environ if no secrets manager
                        import os
                        env_name = secret_name.upper().replace("-", "_")
                        resolved_env[k] = os.environ.get(env_name, "")
                else:
                    # Plaintext value
                    resolved_env[k] = v

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=resolved_env,
        )

        transport = stdio_client(server_params)
        read_stream, write_stream = await transport.__aenter__()
        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()
        await session.initialize()

        self._sessions.append((transport, session))
        return session

    def get_clients(self) -> dict[str, Any]:
        """Return connected MCP client sessions."""
        return dict(self._clients)

    def get_all_tool_schemas(self) -> dict[str, list[dict]]:
        """Return discovered tool schemas keyed by server name."""
        return dict(self._tool_schemas)

    def get_server_configs(self) -> list[MCPServerConfig]:
        """Return parsed server configurations."""
        return list(self._server_configs)

    async def disconnect_one(self, name: str) -> bool:
        """Disconnect a single MCP server and drop its discovered tools.

        Returns True if the server was connected. The dict entries are removed
        unconditionally (so tools stop resolving immediately); the underlying
        stdio transport is closed best-effort — a cross-task cancel scope is
        benign and logged at debug, matching ``disconnect_all``.
        """
        session = self._clients.pop(name, None)
        self._tool_schemas.pop(name, None)
        if session is None:
            return False
        remaining = []
        for transport, sess in self._sessions:
            if sess is session:
                try:
                    await sess.__aexit__(None, None, None)
                    await transport.__aexit__(None, None, None)
                except Exception as e:
                    if "cancel scope in a different task" in str(e):
                        logger.debug("MCP '%s' cross-task teardown (benign): %s", name, e)
                    else:
                        logger.warning("Error disconnecting MCP '%s': %s", name, e)
            else:
                remaining.append((transport, sess))
        self._sessions = remaining
        logger.info("MCP disconnected on demand: %s", name)
        return True

    async def disconnect_all(self):
        """Gracefully disconnect all MCP servers."""
        for transport, session in self._sessions:
            try:
                await session.__aexit__(None, None, None)
                await transport.__aexit__(None, None, None)
            except Exception as e:
                # Benign anyio cross-task teardown (the stdio transport's cancel
                # scope is task-bound). Not a real failure — see ClientMCPManager.
                if "cancel scope in a different task" in str(e):
                    logger.debug("MCP server cross-task teardown (benign): %s", e)
                else:
                    logger.warning("Error disconnecting MCP server: %s", e)
        self._sessions.clear()
        self._clients.clear()
        self._tool_schemas.clear()
        logger.info("All MCP servers disconnected")
