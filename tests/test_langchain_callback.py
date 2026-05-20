"""Tests for ForgeOS LangChain/LangGraph kernel callback."""

import pytest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from stacks.langchain.callback import ForgeOSKernelCallback, LANGCHAIN_AVAILABLE


# Skip all tests if langchain-core not installed
pytestmark = pytest.mark.skipif(
    not LANGCHAIN_AVAILABLE, reason="langchain-core not installed"
)


class TestForgeOSKernelCallback:
    """Test the callback handler in isolation (no real HTTP)."""

    def _make_callback(self, kernel_response=None, **kwargs):
        """Create a callback with a mocked kernel."""
        cb = ForgeOSKernelCallback(
            forgeos_url="https://forgeos.test",
            agent_id="test-agent",
            **kwargs,
        )
        if kernel_response is not None:
            cb._check_kernel = MagicMock(return_value=kernel_response)
        return cb

    def test_allow_passes_silently(self):
        cb = self._make_callback({"action": "allow", "reason": "permitted"})
        cb.on_tool_start(
            {"name": "search"}, "query", run_id=uuid4(),
        )

    def test_deny_raises_tool_exception(self):
        from langchain_core.tools import ToolException
        cb = self._make_callback({"action": "deny", "reason": "tool not in allowed list"})
        with pytest.raises(ToolException, match="ForgeOS denied"):
            cb.on_tool_start(
                {"name": "send_email"}, '{"to": "all"}', run_id=uuid4(),
            )

    def test_rate_limit_raises_tool_exception(self):
        from langchain_core.tools import ToolException
        cb = self._make_callback({"action": "rate_limit", "reason": "daily budget exceeded"})
        with pytest.raises(ToolException, match="rate limited"):
            cb.on_tool_start(
                {"name": "expensive_tool"}, "{}", run_id=uuid4(),
            )

    def test_kernel_failure_allows_by_default(self):
        cb = ForgeOSKernelCallback(agent_id="test")
        cb._check_kernel = MagicMock(side_effect=ConnectionError("offline"))
        # Should not raise — fails open
        cb.on_tool_start(
            {"name": "search"}, "query", run_id=uuid4(),
        )

    def test_extracts_tool_name_from_serialized(self):
        cb = self._make_callback({"action": "allow"})
        cb.on_tool_start(
            {"name": "platform__crm_search"}, "input", run_id=uuid4(),
        )
        cb._check_kernel.assert_called_once_with("platform__crm_search", {})

    def test_passes_inputs_to_kernel(self):
        cb = self._make_callback({"action": "allow"})
        cb.on_tool_start(
            {"name": "search"},
            "query",
            run_id=uuid4(),
            inputs={"query": "fintech leads", "limit": 10},
        )
        cb._check_kernel.assert_called_once_with(
            "search", {"query": "fintech leads", "limit": 10},
        )

    def test_raise_error_is_true(self):
        cb = ForgeOSKernelCallback(agent_id="test")
        assert cb.raise_error is True


class TestKernelCheckModes:
    """Test the three kernel check paths (in-process, runtime, HTTP)."""

    def test_mode_a_direct_kernel(self):
        """Mode A: kernel passed directly — no HTTP."""
        mock_kernel = MagicMock()
        mock_decision = MagicMock()
        mock_decision.to_dict.return_value = {"action": "allow", "reason": "ok"}
        mock_kernel.check_tool_call.return_value = mock_decision

        cb = ForgeOSKernelCallback(agent_id="test", kernel=mock_kernel)
        result = cb._check_kernel("search", {"q": "test"})

        mock_kernel.check_tool_call.assert_called_once_with("test", "search", {"q": "test"})
        assert result["action"] == "allow"

    def test_mode_c_http_fallback(self):
        """Mode C: HTTP call to remote kernel."""
        cb = ForgeOSKernelCallback(
            forgeos_url="https://forgeos.test",
            agent_id="test",
            api_key="fos_test_123",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {"action": "deny", "reason": "not allowed"}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = cb._check_kernel("send_email", {"to": "all"})

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == "https://forgeos.test/api/platform/kernel/check-tool"
        assert call_kwargs[1]["json"]["tool_name"] == "send_email"
        assert call_kwargs[1]["headers"]["X-API-Key"] == "fos_test_123"
        assert result["action"] == "deny"

    def test_no_kernel_no_url_allows(self):
        """No kernel and no URL → allow by default."""
        cb = ForgeOSKernelCallback(agent_id="test")
        result = cb._check_kernel("anything", {})
        assert result["action"] == "allow"


class TestCallbackWithBaseTool:
    """Test that the callback integrates with LangChain's BaseTool."""

    def test_callback_blocks_tool_via_exception(self):
        from langchain_core.tools import BaseTool, ToolException
        from langchain_core.callbacks import CallbackManager

        class DummyTool(BaseTool):
            name: str = "dangerous_tool"
            description: str = "A dangerous tool"

            def _run(self, **kwargs):
                return "executed"

        tool = DummyTool()
        cb = ForgeOSKernelCallback(agent_id="test")
        cb._check_kernel = MagicMock(
            return_value={"action": "deny", "reason": "forbidden"}
        )

        with pytest.raises(ToolException, match="ForgeOS denied"):
            tool.run(
                {"input": "test"},
                callbacks=[cb],
            )

    def test_callback_allows_tool_execution(self):
        from langchain_core.tools import BaseTool

        class SafeTool(BaseTool):
            name: str = "safe_tool"
            description: str = "A safe tool"

            def _run(self, **kwargs):
                return "safe result"

        tool = SafeTool()
        cb = ForgeOSKernelCallback(agent_id="test")
        cb._check_kernel = MagicMock(
            return_value={"action": "allow", "reason": "ok"}
        )

        result = tool.run(
            {"input": "test"},
            callbacks=[cb],
        )
        assert result == "safe result"
