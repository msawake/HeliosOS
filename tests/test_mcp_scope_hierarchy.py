"""Permission-scoped MCP aggregation + per-server tool allow/deny.

Covers the LiteLLM-style governance added to the agent MCP path:
  * `_mcp_scope_chain` — an agent aggregates MCPs from its own scope + broader,
    never a more-private one (a shared/namespace agent must never see `user:*`).
  * `tool_permitted` / `filter_tool_schemas` — per-server allow/deny.
  * `append_client_mcp_tools` — aggregates across the chain with narrowest-wins
    dedupe by server_name, and honors allow/deny (applied in the manager).
  * `_execute_mcp_tool` — enforces allow/deny at the execution funnel and routes
    a server to the scope that owns it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from stacks.base import OwnershipType, _mcp_scope_chain
from src.mcp.client_mcp_manager import filter_tool_schemas, tool_permitted
from src.platform.agentic_loop import append_client_mcp_tools


def _agent(ownership, namespace):
    return SimpleNamespace(ownership=ownership, namespace=namespace)


# --------------------------------------------------------------------------- #
# _mcp_scope_chain — the permission hierarchy
# --------------------------------------------------------------------------- #

def test_personal_chain_is_user_then_ns_then_platform():
    chain = _mcp_scope_chain(_agent(OwnershipType.PERSONAL, "eng"), "U", "user:U")
    assert chain == ["user:U", "ns:eng", "_platform"]


def test_personal_default_namespace_folds_into_platform():
    chain = _mcp_scope_chain(_agent(OwnershipType.PERSONAL, "default"), "U", "user:U")
    assert chain == ["user:U", "_platform"]  # no phantom ns:default


def test_shared_agent_never_sees_user_scope():
    chain = _mcp_scope_chain(_agent(OwnershipType.SHARED, "eng"), "default", None)
    assert chain == ["ns:eng", "_platform"]
    assert not any(c.startswith("user:") for c in chain)


def test_tenant_agent_is_platform_only():
    chain = _mcp_scope_chain(_agent(OwnershipType.SHARED, "default"), "default", None)
    assert chain == ["_platform"]


def test_client_ownership_seeds_head_no_user_no_ns():
    chain = _mcp_scope_chain(_agent(OwnershipType.CLIENT, "eng"), "default", "acme")
    assert chain == ["acme", "_platform"]


# --------------------------------------------------------------------------- #
# tool_permitted / filter_tool_schemas
# --------------------------------------------------------------------------- #

def test_tool_permitted_allow_all_when_none():
    assert tool_permitted("anything", {"allowed_tools": None, "disallowed_tools": []})


def test_tool_permitted_allowlist():
    cfg = {"allowed_tools": ["a", "b"], "disallowed_tools": []}
    assert tool_permitted("a", cfg)
    assert not tool_permitted("c", cfg)


def test_tool_permitted_denylist_wins():
    cfg = {"allowed_tools": None, "disallowed_tools": ["danger"]}
    assert not tool_permitted("danger", cfg)
    assert tool_permitted("safe", cfg)


def test_filter_tool_schemas_subtracts():
    schemas = [{"name": "a"}, {"name": "b"}, {"name": "danger"}]
    cfg = {"allowed_tools": None, "disallowed_tools": ["danger"]}
    assert [s["name"] for s in filter_tool_schemas(schemas, cfg)] == ["a", "b"]


# --------------------------------------------------------------------------- #
# append_client_mcp_tools — aggregation + narrowest-wins dedupe
# --------------------------------------------------------------------------- #

class _FakeManager:
    """Minimal stand-in whose get_all_client_tools returns per-scope tool maps."""

    def __init__(self, by_client, access_groups=None):
        self._by_client = by_client
        self._access_groups = access_groups or {}

    async def get_all_client_tools(self, client_id):
        return self._by_client.get(client_id, {})

    def resolve_access_group(self, name):
        v = self._access_groups.get(name)
        return set(v) if v is not None else None


class _FakeExecutor:
    def __init__(self, manager):
        self._client_mcp_manager = manager


@pytest.mark.asyncio
async def test_aggregates_across_chain():
    mgr = _FakeManager({
        "user:U": {"jira": [{"name": "getIssue", "description": "", "inputSchema": {}}]},
        "_platform": {"github": [{"name": "listRepos", "description": "", "inputSchema": {}}]},
    })
    tools = await append_client_mcp_tools([], _FakeExecutor(mgr), ["user:U", "_platform"], None)
    names = sorted(t["name"] for t in tools)
    assert names == ["mcp__github__listRepos", "mcp__jira__getIssue"]


@pytest.mark.asyncio
async def test_narrowest_scope_shadows_broader_for_same_server():
    # Both user and platform register a `jira` server; the user's private one wins.
    mgr = _FakeManager({
        "user:U": {"jira": [{"name": "userTool", "description": "", "inputSchema": {}}]},
        "_platform": {"jira": [{"name": "tenantTool", "description": "", "inputSchema": {}}]},
    })
    tools = await append_client_mcp_tools([], _FakeExecutor(mgr), ["user:U", "_platform"], None)
    names = [t["name"] for t in tools]
    assert names == ["mcp__jira__userTool"]  # tenant jira fully shadowed


@pytest.mark.asyncio
async def test_shared_agent_chain_yields_no_user_tools():
    # A shared agent's chain has no user: scope, so even if a user MCP exists it
    # is never fetched.
    mgr = _FakeManager({
        "user:U": {"jira": [{"name": "secret", "description": "", "inputSchema": {}}]},
        "ns:eng": {"slack": [{"name": "post", "description": "", "inputSchema": {}}]},
        "_platform": {},
    })
    tools = await append_client_mcp_tools([], _FakeExecutor(mgr), ["ns:eng", "_platform"], None)
    names = [t["name"] for t in tools]
    assert names == ["mcp__slack__post"]
    assert all("jira" not in n for n in names)


# --------------------------------------------------------------------------- #
# access groups — narrowing the in-scope servers
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_access_group_narrows_to_listed_servers():
    mgr = _FakeManager(
        {"user:U": {
            "jira": [{"name": "getIssue", "description": "", "inputSchema": {}}],
            "slack": [{"name": "post", "description": "", "inputSchema": {}}],
        }},
        access_groups={"support": ["jira"]},  # slack excluded
    )
    tools = await append_client_mcp_tools(
        [], _FakeExecutor(mgr), ["user:U"], None, access_group="support",
    )
    assert [t["name"] for t in tools] == ["mcp__jira__getIssue"]


@pytest.mark.asyncio
async def test_missing_access_group_means_no_restriction():
    # A group name that doesn't resolve (None) must NOT hide everything — it
    # falls back to all in-scope servers.
    mgr = _FakeManager(
        {"user:U": {"jira": [{"name": "getIssue", "description": "", "inputSchema": {}}]}},
        access_groups={},  # 'ghost' resolves to None
    )
    tools = await append_client_mcp_tools(
        [], _FakeExecutor(mgr), ["user:U"], None, access_group="ghost",
    )
    assert [t["name"] for t in tools] == ["mcp__jira__getIssue"]


@pytest.mark.asyncio
async def test_empty_access_group_masks_everything():
    # An existing but empty group is a valid way to fully mask MCPs.
    mgr = _FakeManager(
        {"user:U": {"jira": [{"name": "getIssue", "description": "", "inputSchema": {}}]}},
        access_groups={"locked": []},
    )
    tools = await append_client_mcp_tools(
        [], _FakeExecutor(mgr), ["user:U"], None, access_group="locked",
    )
    assert tools == []
