"""Tests for ForgeOS MCP Server — tool, resource, and prompt registration."""

from __future__ import annotations

import pytest


class TestMCPServerRegistration:
    """Verify all MCP tools, resources, and prompts are registered."""

    def test_server_imports(self):
        from src.forgeos_mcp.server import server
        assert server.name == "forgeos"

    def test_tool_count(self):
        from src.forgeos_mcp.server import server
        tools = server._tool_manager._tools
        assert len(tools) >= 23

    def test_chat_tools(self):
        from src.forgeos_mcp.server import server
        tools = server._tool_manager._tools
        assert "forgeos_list_agents" in tools
        assert "forgeos_agent_detail" in tools
        assert "forgeos_chat" in tools
        assert "forgeos_chat_history" in tools

    def test_hitl_tools(self):
        from src.forgeos_mcp.server import server
        tools = server._tool_manager._tools
        assert "forgeos_pending_approvals" in tools
        assert "forgeos_approve" in tools
        assert "forgeos_reject" in tools
        assert "forgeos_a2h_pending" in tools
        assert "forgeos_a2h_respond" in tools
        assert "forgeos_audit_log" in tools
        assert "forgeos_agent_contract" in tools

    def test_fleet_tools(self):
        from src.forgeos_mcp.server import server
        tools = server._tool_manager._tools
        assert "forgeos_health" in tools
        assert "forgeos_fleet_status" in tools
        assert "forgeos_process_table" in tools
        assert "forgeos_budget_overview" in tools
        assert "forgeos_deploy" in tools
        assert "forgeos_deploy_yaml" in tools
        assert "forgeos_undeploy" in tools
        assert "forgeos_stop" in tools
        assert "forgeos_signal" in tools

    def test_invoke_tools(self):
        from src.forgeos_mcp.server import server
        tools = server._tool_manager._tools
        assert "forgeos_invoke" in tools
        assert "forgeos_fire_event" in tools
        assert "forgeos_billing_usage" in tools

    def test_resources(self):
        from src.forgeos_mcp.server import server
        resources = server._resource_manager._resources
        assert "forgeos://fleet" in resources
        assert "forgeos://health" in resources
        assert "forgeos://budgets" in resources
        assert "forgeos://audit" in resources
        assert "forgeos://approvals" in resources

    def test_prompts(self):
        from src.forgeos_mcp.server import server
        prompts = server._prompt_manager._prompts
        assert "review_approvals" in prompts
        assert "fleet_report" in prompts
        assert "agent_diagnostics" in prompts

    def test_main_module_imports(self):
        from src.forgeos_mcp.__main__ import main
        assert callable(main)
