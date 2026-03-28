"""Tests for MCP server manager and tool registration."""

import pytest

from src.mcp.server_manager import MCPServerConfig, MCPServerManager
from src.mcp.tool_executor import ToolExecutor


# ── Config Parsing ───────────────────────────────────────────────────────


class TestConfigParsing:
    def test_parse_tiered_config(self):
        config = {
            "mcp_servers": {
                "tier1": [
                    {"name": "google-workspace", "package": "google-workspace-mcp", "required": True},
                    {"name": "slack", "package": "@anthropic/mcp-server-slack", "required": True},
                ],
                "tier2": [
                    {"name": "analytics", "package": "analytics-mcp", "required": False},
                ],
            }
        }
        mgr = MCPServerManager(config)
        configs = mgr.get_server_configs()
        assert len(configs) == 3
        assert configs[0].name == "google-workspace"
        assert configs[0].required is True
        assert configs[0].tier == 1
        assert configs[2].name == "analytics"
        assert configs[2].required is False
        assert configs[2].tier == 2

    def test_empty_config(self):
        mgr = MCPServerManager({})
        assert mgr.get_server_configs() == []

    def test_no_mcp_servers_key(self):
        config = {"budgets": {"daily_token_budget": 1000}}
        mgr = MCPServerManager(config)
        assert mgr.get_server_configs() == []

    def test_none_config(self):
        mgr = MCPServerManager(None)
        assert mgr.get_server_configs() == []

    def test_tier3_parsed(self):
        config = {
            "mcp_servers": {
                "tier3": [
                    {"name": "datadog", "package": "datadog-mcp", "required": False},
                ],
            }
        }
        mgr = MCPServerManager(config)
        configs = mgr.get_server_configs()
        assert len(configs) == 1
        assert configs[0].tier == 3

    def test_env_vars_parsed(self):
        config = {
            "mcp_servers": {
                "tier1": [
                    {
                        "name": "postgres",
                        "package": "@modelcontextprotocol/server-postgres",
                        "required": True,
                        "env_vars": {"DATABASE_URL": "postgres://localhost/db"},
                    },
                ],
            }
        }
        mgr = MCPServerManager(config)
        configs = mgr.get_server_configs()
        assert configs[0].env_vars == {"DATABASE_URL": "postgres://localhost/db"}


# ── Tool Schema Discovery (offline) ─────────────────────────────────────


class TestToolSchemas:
    def test_no_connections_empty_schemas(self):
        mgr = MCPServerManager({})
        assert mgr.get_all_tool_schemas() == {}

    def test_get_clients_empty(self):
        mgr = MCPServerManager({})
        assert mgr.get_clients() == {}


# ── Connect (graceful degradation) ──────────────────────────────────────


class TestConnectGraceful:
    @pytest.mark.asyncio
    async def test_connect_all_without_mcp_sdk(self):
        """If mcp SDK is not installed, connect_all returns empty dict."""
        config = {
            "mcp_servers": {
                "tier1": [
                    {"name": "slack", "package": "@anthropic/mcp-server-slack", "required": True},
                ],
            }
        }
        mgr = MCPServerManager(config)
        clients = await mgr.connect_all()
        # Without the mcp SDK actually installed and servers running,
        # this returns empty (graceful degradation)
        assert isinstance(clients, dict)


# ── Tool Registration in ToolExecutor ────────────────────────────────────


class TestToolRegistration:
    def test_register_mcp_tools(self):
        executor = ToolExecutor()
        schemas = [
            {"name": "send_gmail_message", "description": "Send email", "inputSchema": {"type": "object"}},
            {"name": "search_gmail_messages", "description": "Search email", "inputSchema": {"type": "object"}},
        ]
        executor.register_mcp_tools("google-workspace", schemas)
        defs = executor.get_mcp_tool_definitions()
        assert len(defs) == 2
        assert defs[0]["name"] == "mcp__google-workspace__send_gmail_message"
        assert defs[1]["name"] == "mcp__google-workspace__search_gmail_messages"

    def test_register_multiple_servers(self):
        executor = ToolExecutor()
        executor.register_mcp_tools("slack", [
            {"name": "post_message", "description": "Post to Slack"},
        ])
        executor.register_mcp_tools("stripe", [
            {"name": "create_invoice", "description": "Create invoice"},
            {"name": "list_customers", "description": "List customers"},
        ])
        defs = executor.get_mcp_tool_definitions()
        assert len(defs) == 3
        names = {d["name"] for d in defs}
        assert "mcp__slack__post_message" in names
        assert "mcp__stripe__create_invoice" in names
        assert "mcp__stripe__list_customers" in names

    def test_empty_registration(self):
        executor = ToolExecutor()
        assert executor.get_mcp_tool_definitions() == []

    def test_tool_definitions_have_input_schema(self):
        executor = ToolExecutor()
        schemas = [
            {
                "name": "query",
                "description": "Run SQL query",
                "inputSchema": {
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                },
            },
        ]
        executor.register_mcp_tools("postgres", schemas)
        defs = executor.get_mcp_tool_definitions()
        assert defs[0]["input_schema"]["properties"]["sql"]["type"] == "string"

    def test_tool_filtering_with_wildcards(self):
        """Verify that _tool_matches in AgentInvoker works with MCP tool names."""
        from src.core.agent_invoker import AgentInvoker

        allowed = ["mcp__google-workspace__*", "mcp__postgres__query"]

        assert AgentInvoker._tool_matches("mcp__google-workspace__send_gmail_message", allowed)
        assert AgentInvoker._tool_matches("mcp__google-workspace__search_gmail_messages", allowed)
        assert AgentInvoker._tool_matches("mcp__postgres__query", allowed)
        assert not AgentInvoker._tool_matches("mcp__postgres__write", allowed)
        assert not AgentInvoker._tool_matches("mcp__slack__post_message", allowed)
