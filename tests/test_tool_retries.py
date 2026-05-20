"""Tests for agentic loop tool retry + timeout logic."""

from __future__ import annotations

import asyncio

import pytest

from src.platform.agentic_loop import (
    TOOL_DEFAULT_TIMEOUT_SECONDS,
    _execute_tool,
    _tool_timeout_for,
)


class _FakeExecutor:
    """A fake tool executor that tracks invocations."""

    def __init__(self, behaviors):
        """
        behaviors: callable that takes (call_count) and returns the execute() body.
        Can return a dict, raise, or sleep.
        """
        self._behaviors = behaviors
        self.call_count = 0

    async def execute(self, tool_name, tool_input, agent_context):
        self.call_count += 1
        return await self._behaviors(self.call_count, tool_name, tool_input)


class TestExecuteTool:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        async def ok(n, name, inp):
            return {"success": True, "result": "done"}
        executor = _FakeExecutor(ok)
        r = await _execute_tool("t1", {}, executor, None, timeout=5, max_retries=2)
        assert r == {"success": True, "result": "done"}
        assert executor.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_exception(self):
        async def flaky(n, name, inp):
            if n < 3:
                raise ConnectionError("transient network error")
            return {"success": True, "result": "finally"}
        executor = _FakeExecutor(flaky)
        r = await _execute_tool("t1", {}, executor, None, timeout=5, max_retries=3)
        assert r == {"success": True, "result": "finally"}
        assert executor.call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_timeout(self):
        async def slow(n, name, inp):
            if n < 2:
                await asyncio.sleep(10)  # will time out
            return {"success": True, "result": "ok"}
        executor = _FakeExecutor(slow)
        r = await _execute_tool("t1", {}, executor, None, timeout=0.05, max_retries=2)
        assert r.get("success") is True
        assert executor.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries_returns_error(self):
        async def always_fail(n, name, inp):
            raise ValueError("nope")
        executor = _FakeExecutor(always_fail)
        r = await _execute_tool("t1", {}, executor, None, timeout=5, max_retries=2)
        assert "error" in r
        assert "nope" in r["error"]
        assert executor.call_count == 3  # original + 2 retries

    @pytest.mark.asyncio
    async def test_does_not_retry_on_error_dict(self):
        """When executor returns {'error': ...}, that's an intentional failure — no retry."""
        async def returns_error(n, name, inp):
            return {"success": False, "error": "deliberate"}
        executor = _FakeExecutor(returns_error)
        r = await _execute_tool("t1", {}, executor, None, timeout=5, max_retries=3)
        assert r == {"success": False, "error": "deliberate"}
        assert executor.call_count == 1  # no retries

    @pytest.mark.asyncio
    async def test_no_executor_returns_error(self):
        r = await _execute_tool("t1", {}, None, None, timeout=5)
        assert "error" in r

    @pytest.mark.asyncio
    async def test_timeout_with_no_retries(self):
        async def slow(n, name, inp):
            await asyncio.sleep(10)
            return {"ok": True}
        executor = _FakeExecutor(slow)
        r = await _execute_tool("t1", {}, executor, None, timeout=0.05, max_retries=0)
        assert "error" in r
        assert "timed out" in r["error"].lower()
        assert executor.call_count == 1


class TestToolTimeoutLookup:
    def test_default_when_no_definitions(self):
        assert _tool_timeout_for("foo", None) == TOOL_DEFAULT_TIMEOUT_SECONDS

    def test_default_when_tool_not_listed(self):
        defs = [{"name": "other_tool", "timeout_seconds": 10}]
        assert _tool_timeout_for("foo", defs) == TOOL_DEFAULT_TIMEOUT_SECONDS

    def test_returns_custom_timeout(self):
        defs = [
            {"name": "fast_tool", "timeout_seconds": 5.0},
            {"name": "slow_tool", "timeout_seconds": 300.0},
        ]
        assert _tool_timeout_for("fast_tool", defs) == 5.0
        assert _tool_timeout_for("slow_tool", defs) == 300.0
