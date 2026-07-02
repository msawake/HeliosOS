"""Failure-isolation regression tests for ClientMCPManager.get_all_client_tools.

A bad user MCP (missing binary, dead HTTP endpoint, misbehaving anyio cleanup
in ``stdio_client``) used to be able to raise up through the sequential
per-server discovery loop and cancel the whole Celery task. The
gather-with-per-server-catch shape keeps failures scoped.
"""
from __future__ import annotations

import pytest

from src.mcp.client_mcp_manager import ClientMCPManager


@pytest.mark.asyncio
async def test_one_server_raises_others_survive(monkeypatch):
    mgr = ClientMCPManager(db_client=None, tenant_id="t")
    cid = "user:u"
    mgr.register_client_config(
        cid,
        [
            {"server_name": "good", "package": "x", "enabled": True, "transport": "stdio"},
            {"server_name": "bad",  "package": "y", "enabled": True, "transport": "stdio"},
            {"server_name": "also", "package": "z", "enabled": True, "transport": "stdio"},
        ],
    )

    async def fake_get_tool_schemas(_client_id, server_name, namespace="default"):
        if server_name == "bad":
            # Simulate the exact class that leaks from mcp.client.stdio's cancel-scope
            # cleanup on a subprocess handshake failure.
            raise RuntimeError(
                "Attempted to exit cancel scope in a different task than it was entered in"
            )
        return [{"name": f"{server_name}_tool", "description": "", "inputSchema": {}}]

    monkeypatch.setattr(mgr, "get_tool_schemas", fake_get_tool_schemas)

    result = await mgr.get_all_client_tools(cid)

    # `bad` is silently dropped; the other two are present with their schemas.
    assert set(result) == {"good", "also"}
    assert result["good"][0]["name"] == "good_tool"
    assert result["also"][0]["name"] == "also_tool"


@pytest.mark.asyncio
async def test_all_servers_ok(monkeypatch):
    mgr = ClientMCPManager(db_client=None, tenant_id="t")
    cid = "user:u"
    mgr.register_client_config(
        cid,
        [{"server_name": "a", "package": "x", "enabled": True, "transport": "stdio"}],
    )

    async def fake_get_tool_schemas(_cid, name, namespace="default"):
        return [{"name": f"{name}_tool", "description": "", "inputSchema": {}}]

    monkeypatch.setattr(mgr, "get_tool_schemas", fake_get_tool_schemas)

    result = await mgr.get_all_client_tools(cid)
    assert list(result) == ["a"]


@pytest.mark.asyncio
async def test_empty_returns_dict(monkeypatch):
    mgr = ClientMCPManager(db_client=None, tenant_id="t")
    result = await mgr.get_all_client_tools("user:nobody")
    assert result == {}
