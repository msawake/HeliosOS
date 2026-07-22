"""Regression: a FAILED MCP connect must tear its transport down in-task.

A persisted MCP with a bad package spec (e.g. empty → uvx never launches the
subprocess) fails at ``session.initialize()`` with "Connection closed". If the
already-``__aenter__``'d stdio transport is left open, anyio exits its
task-bound cancel scope later in a different task and raises
``RuntimeError: Attempted to exit cancel scope in a different task`` into
whatever coroutine is running (historically ``bootstrap.boot`` → the whole boot
died). The fix closes the transport in-task on failure; this guards it.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import src.mcp.server_manager as sm

pytestmark = pytest.mark.skipif(not sm.HAS_MCP, reason="MCP SDK not installed")


class _FakeTransport:
    def __init__(self):
        self.exited = False

    async def __aenter__(self):
        return ("read", "write")

    async def __aexit__(self, *a):
        self.exited = True


async def test_failed_connect_closes_transport_in_task():
    mgr = sm.MCPServerManager(config=MagicMock(mcp_servers={}), secrets_manager=None)

    transport = _FakeTransport()
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.initialize = AsyncMock(side_effect=RuntimeError("Connection closed"))

    cfg = sm.MCPServerConfig(name="bigquery", package="", env_vars={}, args=[], transport="stdio")

    with patch.object(sm, "stdio_client", return_value=transport), \
         patch.object(sm, "ClientSession", return_value=session), \
         patch.object(sm, "resolve_launch_command", return_value=("uvx", [])), \
         patch.object(sm, "materialize_gcp_credentials", side_effect=lambda e: e):
        with pytest.raises(RuntimeError, match="Connection closed"):
            await mgr._connect_server(cfg)

    assert transport.exited, "transport must be torn down in-task on failed connect"
    assert (transport, session) not in mgr._sessions, "failed connect must not register a session"
