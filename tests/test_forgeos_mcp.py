"""Tests for Helios OS MCP Server — tool, resource, and prompt registration."""

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
        assert "forgeos_run_status" in tools
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


# ---------------------------------------------------------------------------
# Standalone server (tools/forgeos-mcp-server.py) + parity with the package
# ---------------------------------------------------------------------------

import importlib.util  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402

_STANDALONE_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "forgeos-mcp-server.py"


def _load_standalone():
    spec = importlib.util.spec_from_file_location("forgeos_mcp_standalone", _STANDALONE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def standalone():
    return _load_standalone()


class TestStandaloneParity:
    """The standalone file and the package must expose the same MCP surface."""

    def test_standalone_registers(self, standalone):
        assert standalone.server.name == "forgeos"
        assert len(standalone.server._tool_manager._tools) >= 23

    def test_tool_parity(self, standalone):
        from src.forgeos_mcp.server import server as pkg
        assert set(standalone.server._tool_manager._tools) == set(pkg._tool_manager._tools)

    def test_resource_parity(self, standalone):
        from src.forgeos_mcp.server import server as pkg
        assert {str(k) for k in standalone.server._resource_manager._resources} == \
            {str(k) for k in pkg._resource_manager._resources}

    def test_prompt_parity(self, standalone):
        from src.forgeos_mcp.server import server as pkg
        assert set(standalone.server._prompt_manager._prompts) == set(pkg._prompt_manager._prompts)

    def test_standalone_main(self, standalone):
        assert callable(standalone.main)


# ---------------------------------------------------------------------------
# Tool behaviour against a fake httpx transport (run on the standalone module;
# its tool bodies are identical to the package's)
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.text = json.dumps(data)

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, path, headers=None):
        self.calls.append({"method": "GET", "path": path, "body": None, "headers": headers})
        return _FakeResp({"ok": True})

    async def post(self, path, json=None, content=None, headers=None):
        self.calls.append({"method": "POST", "path": path,
                           "body": json if json is not None else content, "headers": headers})
        return _FakeResp({"ok": True})

    async def delete(self, path, headers=None):
        self.calls.append({"method": "DELETE", "path": path, "body": None, "headers": headers})
        return _FakeResp({"ok": True})


@pytest.fixture
def calls(standalone, monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(standalone.httpx, "AsyncClient", lambda *a, **k: _FakeClient(recorded))
    return recorded


async def test_approve_sends_approved_by(standalone, calls):
    await standalone.forgeos_approve("req-1", reason="lgtm", approved_by="alice")
    call = calls[-1]
    assert call["path"] == "/api/approvals/req-1/approve"
    assert call["body"]["approved_by"] == "alice"
    assert call["body"]["reason"] == "lgtm"


async def test_reject_sends_rejected_by(standalone, calls):
    await standalone.forgeos_reject("req-2", reason="no", rejected_by="bob")
    call = calls[-1]
    assert call["path"] == "/api/approvals/req-2/reject"
    assert call["body"]["rejected_by"] == "bob"


async def test_deploy_yaml_sends_raw_body(standalone, calls):
    manifest = "apiVersion: agentos/v1\nkind: AgentContract\n"
    await standalone.forgeos_deploy_yaml(manifest)
    call = calls[-1]
    assert call["path"] == "/api/platform/agents/from-yaml"
    # raw string body (not a JSON wrapper) + text/yaml content type
    assert call["body"] == manifest
    assert call["headers"]["Content-Type"] == "text/yaml"


async def test_acting_user_header(standalone, calls):
    await standalone.forgeos_invoke("agent-x", "do a thing", acting_user="carol")
    call = calls[-1]
    assert call["path"] == "/api/platform/agents/agent-x/invoke"
    assert call["headers"].get("X-Forgeos-User") == "carol"
    assert call["body"]["prompt"] == "do a thing"
