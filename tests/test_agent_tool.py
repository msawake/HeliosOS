"""Tests for AgentTool pattern (Phase 3b)."""
import pytest
from src.platform.agent_tool import AgentTool, AgentToolConfig, AgentToolRegistry


class TestAgentToolConfig:
    def test_defaults(self):
        cfg = AgentToolConfig(tool_name="ask_researcher", target_agent="researcher")
        assert cfg.target_namespace == "default"
        assert cfg.input_schema["required"] == ["task"]

    def test_from_dict(self):
        cfg = AgentToolConfig.from_dict({
            "name": "ask_cfo",
            "agent": "cfo",
            "namespace": "finance",
            "description": "Ask the CFO for budget approval",
        })
        assert cfg.tool_name == "ask_cfo"
        assert cfg.target_agent == "cfo"
        assert cfg.target_namespace == "finance"
        assert cfg.description == "Ask the CFO for budget approval"

    def test_from_dict_alt_keys(self):
        cfg = AgentToolConfig.from_dict({
            "tool_name": "checker",
            "target_agent": "compliance",
            "target_namespace": "legal",
        })
        assert cfg.tool_name == "checker"
        assert cfg.target_agent == "compliance"


class TestAgentTool:
    def test_to_tool_definition(self):
        tool = AgentTool(AgentToolConfig(
            tool_name="ask_researcher",
            target_agent="researcher",
            description="Delegate research tasks",
        ))
        defn = tool.to_tool_definition()
        assert defn["name"] == "ask_researcher"
        assert defn["description"] == "Delegate research tasks"
        assert "input_schema" in defn

    def test_default_description(self):
        tool = AgentTool(AgentToolConfig(
            tool_name="ask_cfo",
            target_agent="cfo",
            target_namespace="finance",
        ))
        defn = tool.to_tool_definition()
        assert "finance/cfo" in defn["description"]

    def test_name_property(self):
        tool = AgentTool(AgentToolConfig(tool_name="my_tool", target_agent="x"))
        assert tool.name == "my_tool"

    async def test_execute_with_mock_invoker(self):
        class MockInvoker:
            async def invoke(self, agent_id, prompt, context=None, session_id=None):
                return {"output": f"Result for: {prompt}", "status": "completed", "tokens_used": 42}

        tool = AgentTool(AgentToolConfig(tool_name="ask", target_agent="helper"))
        result = await tool.execute({"task": "analyze data"}, MockInvoker())
        assert result["output"] == "Result for: analyze data"
        assert result["status"] == "completed"
        assert result["tokens_used"] == 42

    async def test_execute_failure(self):
        class FailInvoker:
            async def invoke(self, **kwargs):
                raise RuntimeError("agent crashed")

        tool = AgentTool(AgentToolConfig(tool_name="ask", target_agent="bad"))
        result = await tool.execute({"task": "test"}, FailInvoker())
        assert result["status"] == "failed"
        assert "crashed" in result["error"]

    async def test_execute_no_invoker(self):
        tool = AgentTool(AgentToolConfig(tool_name="ask", target_agent="x"))
        result = await tool.execute({"task": "test"}, object())
        assert result["status"] == "failed"


class TestAgentToolRegistry:
    def test_register_and_get(self):
        reg = AgentToolRegistry()
        cfg = AgentToolConfig(tool_name="ask_cfo", target_agent="cfo")
        tool = reg.register(cfg)
        assert reg.get("ask_cfo") is tool
        assert reg.count() == 1

    def test_register_from_manifest(self):
        reg = AgentToolRegistry()
        tools = reg.register_from_manifest([
            {"name": "ask_research", "agent": "researcher"},
            {"name": "ask_legal", "agent": "lawyer", "namespace": "legal"},
        ])
        assert len(tools) == 2
        assert reg.count() == 2
        assert reg.get("ask_research") is not None
        assert reg.get("ask_legal") is not None

    def test_get_tool_definitions(self):
        reg = AgentToolRegistry()
        reg.register(AgentToolConfig(tool_name="t1", target_agent="a1", description="Tool 1"))
        reg.register(AgentToolConfig(tool_name="t2", target_agent="a2", description="Tool 2"))
        defs = reg.get_tool_definitions()
        assert len(defs) == 2
        names = {d["name"] for d in defs}
        assert names == {"t1", "t2"}

    def test_list_tools(self):
        reg = AgentToolRegistry()
        reg.register(AgentToolConfig(tool_name="a", target_agent="x"))
        reg.register(AgentToolConfig(tool_name="b", target_agent="y"))
        assert sorted(reg.list_tools()) == ["a", "b"]

    def test_get_missing(self):
        reg = AgentToolRegistry()
        assert reg.get("nonexistent") is None
