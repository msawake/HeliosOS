"""
Client-scoped MCP server manager.

Unlike MCPServerManager (boot-time, company-wide), this connects
MCP servers on-demand per client and caches them with LRU eviction.

Each client can have its own Jira, Google Analytics, Slack, etc.
with isolated credentials — no cross-client credential leakage.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    HAS_MCP = True
except ImportError:
    HAS_MCP = False


@dataclass
class ClientMCPConnection:
    """A cached MCP server connection for a specific client."""
    client_id: str
    server_name: str
    session: Any  # ClientSession
    transport: Any
    tool_schemas: list[dict] = field(default_factory=list)
    connected_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)


class ClientMCPManager:
    """
    Lazy, per-client MCP server connections with LRU eviction.

    - Connects a client's MCP server on first use
    - Caches the session keyed by (client_id, server_name)
    - Evicts least-recently-used connections when over max_connections
    - Disconnects idle connections after ttl_seconds
    """

    def __init__(
        self,
        db_client=None,
        tenant_id: str = "",
        max_connections: int = 50,
        ttl_seconds: int = 1800,
        secrets_manager: Any | None = None,
    ):
        self._db = db_client
        self._tenant_id = tenant_id
        self._max_connections = max_connections
        self._ttl = ttl_seconds
        self._connections: OrderedDict[tuple[str, str], ClientMCPConnection] = OrderedDict()
        self._lock = asyncio.Lock()
        # In-memory config cache for dev mode (no DB)
        self._config_cache: dict[str, list[dict]] = {}
        # Cooldown: track failed connections to avoid retry storms
        self._connect_cooldowns: dict[tuple[str, str], float] = {}  # key → earliest_retry_time
        self._COOLDOWN_SECONDS = 60.0
        self._secrets_manager = secrets_manager

    def register_client_config(self, client_id: str, configs: list[dict]) -> None:
        """Register MCP configs for a client (in-memory, for dev/no-DB mode)."""
        self._config_cache[client_id] = configs
        logger.info("Registered %d MCP configs for client '%s'", len(configs), client_id)

    async def get_client(self, client_id: str, server_name: str) -> Any | None:
        """Get or lazily connect a client's MCP server session."""
        if not HAS_MCP:
            return None

        key = (client_id, server_name)
        async with self._lock:
            # Check cache
            conn = self._connections.get(key)
            if conn:
                if time.time() - conn.last_used < self._ttl:
                    conn.last_used = time.time()
                    self._connections.move_to_end(key)
                    return conn.session
                else:
                    # Expired — disconnect and reconnect
                    await self._disconnect_one(key)

            # Check cooldown (avoid retry storms after connection failure)
            cooldown_until = self._connect_cooldowns.get(key, 0)
            if time.time() < cooldown_until:
                logger.debug("MCP %s/%s in cooldown until %.0f", client_id, server_name, cooldown_until)
                return None

            # Load config
            config = self._load_server_config(client_id, server_name)
            if not config:
                logger.debug("No MCP config for client '%s' server '%s'", client_id, server_name)
                return None

            # Evict if at capacity
            while len(self._connections) >= self._max_connections:
                await self._evict_oldest()

            # Connect
            try:
                conn = await self._connect(client_id, config)
                if conn:
                    self._connections[key] = conn
                    logger.info(
                        "Client MCP connected: %s/%s (%d tools)",
                        client_id, server_name, len(conn.tool_schemas),
                    )
                    return conn.session
            except Exception as e:
                logger.error("Failed to connect client MCP %s/%s: %s", client_id, server_name, e)
                self._connect_cooldowns[key] = time.time() + self._COOLDOWN_SECONDS
                return None

        return None

    async def get_tool_schemas(self, client_id: str, server_name: str) -> list[dict]:
        """Get tool schemas for a client's MCP server (connects if needed)."""
        key = (client_id, server_name)
        conn = self._connections.get(key)
        if conn:
            return conn.tool_schemas

        # Force connection to discover tools
        await self.get_client(client_id, server_name)
        conn = self._connections.get(key)
        return conn.tool_schemas if conn else []

    async def get_all_client_tools(self, client_id: str) -> dict[str, list[dict]]:
        """Get all tool schemas for all MCP servers configured for a client."""
        configs = self._load_all_configs(client_id)
        result: dict[str, list[dict]] = {}
        for cfg in configs:
            server_name = cfg.get("server_name", "")
            if server_name and cfg.get("enabled", True):
                schemas = await self.get_tool_schemas(client_id, server_name)
                if schemas:
                    result[server_name] = schemas
        return result

    async def disconnect_client(self, client_id: str) -> None:
        """Disconnect all MCP servers for a client."""
        async with self._lock:
            keys_to_remove = [k for k in self._connections if k[0] == client_id]
            for key in keys_to_remove:
                await self._disconnect_one(key)
            logger.info("Disconnected all MCP servers for client '%s'", client_id)

    async def refresh_schemas(self, client_id: str, server_name: str) -> list[dict]:
        """Reconnect and rediscover tools for a client's MCP server."""
        key = (client_id, server_name)
        async with self._lock:
            if key in self._connections:
                await self._disconnect_one(key)
        # Reconnect
        await self.get_client(client_id, server_name)
        conn = self._connections.get(key)
        return conn.tool_schemas if conn else []

    async def disconnect_all(self) -> None:
        """Disconnect all client MCP connections."""
        async with self._lock:
            for key in list(self._connections):
                await self._disconnect_one(key)
            logger.info("All client MCP connections closed")

    def get_stats(self) -> dict:
        """Return connection pool stats."""
        return {
            "active_connections": len(self._connections),
            "max_connections": self._max_connections,
            "ttl_seconds": self._ttl,
            "clients": list({k[0] for k in self._connections}),
        }

    # -- Internal ---------------------------------------------------------

    def _load_server_config(self, client_id: str, server_name: str) -> dict | None:
        """Load a single MCP server config for a client."""
        configs = self._load_all_configs(client_id)
        for cfg in configs:
            if cfg.get("server_name") == server_name and cfg.get("enabled", True):
                return cfg
        return None

    def _load_all_configs(self, client_id: str) -> list[dict]:
        """Load all MCP configs for a client from DB or in-memory cache."""
        # In-memory cache (dev mode)
        if client_id in self._config_cache:
            return self._config_cache[client_id]

        # Try database (tenant-scoped connection — DatabaseClient has no bare
        # .execute(); queries run through db.tenant(...) so RLS applies).
        if self._db and getattr(self._db, "is_connected", False):
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    rows = conn.execute(
                        "SELECT server_name, package, env_vars, args, enabled "
                        "FROM client_mcp_configs WHERE client_id = %s AND enabled = true",
                        (client_id,),
                    )
                return [
                    {
                        "server_name": r["server_name"],
                        "package": r["package"],
                        "env_vars": r.get("env_vars", {}),
                        "args": r.get("args", []),
                        "enabled": r.get("enabled", True),
                    }
                    for r in (rows or [])
                ]
            except Exception as e:
                logger.warning("Failed to load client MCP configs from DB: %s", e)

        return []

    async def _connect(self, client_id: str, config: dict) -> ClientMCPConnection | None:
        """Connect to a single MCP server for a client."""
        package = config.get("package", "")
        server_name = config.get("server_name", "")
        env_vars = config.get("env_vars", {})
        extra_args = config.get("args", [])

        if not package:
            return None

        # Determine command based on package type
        if package.startswith("@") or package.startswith("mcp-server-"):
            command = "npx"
            args = ["-y", package] + extra_args
        else:
            command = "uvx"
            args = [package] + extra_args

        # Resolve environment variables securely at runtime. We always start
        # from a copy of os.environ so the child inherits PATH, HOME, TMPDIR,
        # etc. (the mcp SDK forwards `env` straight to Popen — a bare dict
        # REPLACES the parent env, which causes some MCP servers to start but
        # discover zero tools because dependent state can't be found).
        import os as _os
        resolved_env = _os.environ.copy()
        if env_vars:
            for k, v in env_vars.items():
                if isinstance(v, str) and v.startswith("secret:"):
                    # Extract secret name (e.g., "secret:github-token" -> "github-token")
                    secret_name = v[7:]
                    if self._secrets_manager:
                        resolved_val = self._secrets_manager.get(
                            secret_name,
                            caller=f"client_mcp_{client_id}_{server_name}",
                            reason="client_mcp_boot"
                        )
                        if resolved_val:
                            resolved_env[k] = resolved_val
                        else:
                            logger.warning(f"Secret '{secret_name}' not found for client MCP '{server_name}'")
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
        # uvx/npx-launched servers (esp. heavy Python ones like mcp-atlassian on
        # first run) can take well over 10s to import + handshake. Configurable
        # via FORGEOS_MCP_INIT_TIMEOUT; default generous.
        import os as _os2
        _init_timeout = float(_os2.environ.get("FORGEOS_MCP_INIT_TIMEOUT", "45"))
        try:
            await asyncio.wait_for(session.initialize(), timeout=_init_timeout)
        except asyncio.TimeoutError:
            logger.error(
                "MCP session.initialize() timed out (%.0fs) for %s/%s",
                _init_timeout, client_id, server_name,
            )
            try:
                await session.__aexit__(None, None, None)
                await transport.__aexit__(None, None, None)
            except Exception:
                pass
            return None

        # Discover tools
        tools_response = await session.list_tools()
        tool_schemas = [
            {
                "name": tool.name,
                "description": getattr(tool, "description", ""),
                "inputSchema": getattr(tool, "inputSchema", {}),
            }
            for tool in tools_response.tools
        ]

        return ClientMCPConnection(
            client_id=client_id,
            server_name=server_name,
            session=session,
            transport=transport,
            tool_schemas=tool_schemas,
        )

    async def _disconnect_one(self, key: tuple[str, str]) -> None:
        """Disconnect and remove a single connection."""
        conn = self._connections.pop(key, None)
        if conn:
            try:
                await conn.session.__aexit__(None, None, None)
                await conn.transport.__aexit__(None, None, None)
            except Exception as e:
                # The MCP stdio transport's anyio cancel scope is task-bound; when
                # a connection is established in one task (e.g. a runtime worker)
                # and torn down in another, anyio raises "Attempted to exit cancel
                # scope in a different task". The reconnect on next use succeeds,
                # so this is expected noise rather than a real failure.
                if "cancel scope in a different task" in str(e):
                    logger.debug("client MCP %s/%s cross-task teardown (benign): %s",
                                 key[0], key[1], e)
                else:
                    logger.warning("Error disconnecting client MCP %s/%s: %s", key[0], key[1], e)

    async def _evict_oldest(self) -> None:
        """Evict the least-recently-used connection."""
        if self._connections:
            key, _ = self._connections.popitem(last=False)
            await self._disconnect_one(key)
            logger.debug("Evicted client MCP connection: %s/%s", key[0], key[1])
