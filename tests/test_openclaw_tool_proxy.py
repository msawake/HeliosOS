"""Tests for OpenClaw tool proxy — kernel-gated tool execution."""

import pytest

pytestmark = pytest.mark.kernel

from stacks.openclaw.adapter import OpenClawAdapter, ToolProxyServer
from stacks.sandbox.adapter import get_token_store
from stacks.base import AgentDefinition, ExecutionType, OwnershipType
from src.platform.kernel import Kernel
from src.platform.registry import AgentRegistry
from src.platform.process import ProcessTable
from src.forgeos_sdk.runtime import runtime as _rt


def _make_agent(**overrides):
    defaults = dict(
        name="oc-agent",
        stack="openclaw",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        description="test openclaw agent",
        tools=["company__search_knowledge", "company__publish_event"],
        namespace="sales",
    )
    defaults.update(overrides)
    return AgentDefinition(**defaults)


class TestTokenMinting:
    async def test_create_agent_mints_token(self):
        adapter = OpenClawAdapter(openclaw_dir="/nonexistent")
        agent_def = _make_agent()
        await adapter.create_agent(agent_def)

        assert agent_def.agent_id in adapter._agent_tokens
        token = adapter._agent_tokens[agent_def.agent_id]
        assert token.startswith("sbx_")

        claims = get_token_store().verify(token)
        assert claims is not None
        assert claims["agent_id"] == agent_def.agent_id
        assert claims["namespace"] == "sales"

    async def test_token_contains_tools(self):
        adapter = OpenClawAdapter(openclaw_dir="/nonexistent")
        agent_def = _make_agent()
        await adapter.create_agent(agent_def)

        token = adapter._agent_tokens[agent_def.agent_id]
        claims = get_token_store().verify(token)
        assert "company__search_knowledge" in claims["tools"]
        assert "company__publish_event" in claims["tools"]


class TestWorkspaceWithProxy:
    async def test_soul_contains_proxy_instructions(self, tmp_path, monkeypatch):
        import stacks.openclaw.adapter as oc_mod
        monkeypatch.setattr(oc_mod, "OPENCLAW_STATE_DIR", str(tmp_path))

        adapter = OpenClawAdapter(openclaw_dir="/nonexistent")
        agent_def = _make_agent()
        await adapter.create_agent(agent_def)

        soul = (tmp_path / "workspaces" / "oc-agent" / "SOUL.md").read_text()
        assert "127.0.0.1" in soul
        assert "/tool" in soul
        assert "X-Agent-Token" in soul

    async def test_skills_yaml_has_proxy_endpoints(self, tmp_path, monkeypatch):
        import stacks.openclaw.adapter as oc_mod
        monkeypatch.setattr(oc_mod, "OPENCLAW_STATE_DIR", str(tmp_path))

        adapter = OpenClawAdapter(openclaw_dir="/nonexistent")
        agent_def = _make_agent()
        await adapter.create_agent(agent_def)

        skills = (tmp_path / "workspaces" / "oc-agent" / "SKILLS" / "default.yaml").read_text()
        assert "company__search_knowledge" in skills
        assert "company__publish_event" in skills
        assert "POST" in skills
        assert "/tool" in skills
        assert "X-Agent-Token" in skills


class TestToolProxyProcessing:
    async def test_proxy_allows_valid_tool(self):
        registry = AgentRegistry()
        agent_def = _make_agent()
        agent_id = registry.register(agent_def)
        kernel = Kernel(registry=registry)
        _rt.register_platform(kernel=kernel, process_table=ProcessTable(registry=registry))

        token = get_token_store().mint(agent_def)

        class FakeExecutor:
            async def execute(self, name, inp, ctx):
                return {"result": f"executed {name}"}

        proxy = ToolProxyServer(tool_executor=FakeExecutor())
        result = await proxy._process_tool_call(
            {"tool_name": "company__search_knowledge", "tool_input": {"q": "test"}},
            token,
        )
        assert "error" not in result
        assert result["result"] == "executed company__search_knowledge"

    async def test_proxy_denies_invalid_token(self):
        proxy = ToolProxyServer(tool_executor=None)
        result = await proxy._process_tool_call(
            {"tool_name": "anything", "tool_input": {}},
            "invalid_token_xyz",
        )
        assert "error" in result
        assert "Invalid" in result["error"]

    async def test_proxy_denies_unauthorized_tool(self):
        registry = AgentRegistry()
        agent_def = _make_agent(tools=["company__search_knowledge"],
                                metadata={"_capabilities": {"tools": {"denied": ["company__search_knowledge"]}}})
        agent_id = registry.register(agent_def)
        kernel = Kernel(registry=registry)
        _rt.register_platform(kernel=kernel, process_table=ProcessTable(registry=registry))

        token = get_token_store().mint(agent_def)

        class FakeExecutor:
            async def execute(self, name, inp, ctx):
                return {"result": "should not reach here"}

        proxy = ToolProxyServer(tool_executor=FakeExecutor())
        result = await proxy._process_tool_call(
            {"tool_name": "company__search_knowledge", "tool_input": {}},
            token,
        )
        assert "error" in result
        assert "denied" in result["error"].lower()

    async def test_proxy_denies_unlisted_tool(self):
        registry = AgentRegistry()
        agent_def = _make_agent(tools=["company__search_knowledge"])
        agent_id = registry.register(agent_def)
        kernel = Kernel(registry=registry)
        _rt.register_platform(kernel=kernel, process_table=ProcessTable(registry=registry))

        token = get_token_store().mint(agent_def)

        class FakeExecutor:
            async def execute(self, name, inp, ctx):
                return {"result": "should not reach here"}

        proxy = ToolProxyServer(tool_executor=FakeExecutor())
        result = await proxy._process_tool_call(
            {"tool_name": "shell.exec", "tool_input": {}},
            token,
        )
        assert "error" in result
        assert "denied" in result["error"].lower()
