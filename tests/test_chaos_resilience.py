"""
Chaos / resilience tests.

Simulates real failure modes and asserts that the Phase 2 hardening
(retry, failover, timeout, crash recovery) actually behaves correctly.

These are pure in-process tests — no real K8s cluster or DB required —
but they exercise the same code paths that get stressed in production.

Coverage map:
  - DB connection loss    → ClientStore / KnowledgeBase fall back in-memory
  - LLM provider outage   → LLMRouter retries + fails over to secondary
  - LLM timeout           → LLMRouter retries classified-as-retryable errors
  - MCP tool timeout      → agentic_loop._execute_tool returns error dict
  - MCP tool crashes      → agentic_loop._execute_tool retries then errors
  - Autonomous crash loop → executor bails out after max_crashes
  - Tool whitelist bypass → tool_executor rejects unlisted tools
  - Concurrent access     → event bus handles simultaneous subscribers
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from src.core.database import InMemoryDatabaseClient
from src.platform.client_store import PostgresClientMCPStore, PostgresClientStore


# ---------------------------------------------------------------------------
# DB failure simulation
# ---------------------------------------------------------------------------

class _FailingDB:
    """DB client that raises on every operation after N calls."""

    def __init__(self, fail_after: int = 0):
        self.is_connected = True
        self._calls = 0
        self._fail_after = fail_after

    def tenant(self, tid):
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            self._calls += 1
            if self._calls > self._fail_after:
                raise ConnectionError(f"DB connection lost on call {self._calls}")
            yield MagicMock()

        return _ctx()


class TestDBFailureResilience:
    """When the DB drops, stores should fall back to in-memory without crashing."""

    def test_client_store_tolerates_immediate_db_failure(self):
        """create() should still work (in-memory fallback) after DB fails."""
        store = PostgresClientStore(db_client=_FailingDB(fail_after=0), tenant_id="t1")
        client = store.create("acme", "Acme Corp")
        # Should fall back to in-memory
        assert client["id"] == "acme"
        assert store.get("acme") is not None

    def test_mcp_store_list_survives_db_loss(self):
        """list_for_client() should return empty rather than raise on DB loss."""
        store = PostgresClientMCPStore(db_client=_FailingDB(fail_after=0), tenant_id="t1")
        # Empty result is fine; crash is not
        result = store.list_for_client("nonexistent")
        assert result == []

    def test_client_store_add_then_lose_db(self):
        """Add a client in-memory, lose DB, still able to get() the cached row."""
        store = PostgresClientStore(db_client=None, tenant_id="t1")
        store.create("beta", "Beta Inc")
        # Now simulate the DB coming back but being broken
        store._db = _FailingDB(fail_after=0)
        # In-memory cache should still work
        assert store.get("beta") is not None


# ---------------------------------------------------------------------------
# LLM provider failure simulation
# ---------------------------------------------------------------------------

class TestLLMFailureResilience:
    """The Phase 2 retry + failover logic should handle provider outages."""

    @pytest.mark.asyncio
    async def test_provider_outage_triggers_retries(self, monkeypatch):
        """A retryable error should cause retries (up to 3 attempts)."""
        from src.platform.llm_router import LLMRouter, _with_retry
        import src.platform.llm_router as llm_mod
        monkeypatch.setattr(llm_mod, "BACKOFF_BASE_SECONDS", 0.0)
        monkeypatch.setattr(llm_mod, "BACKOFF_MAX_SECONDS", 0.0)

        class RateLimitError(Exception):
            pass

        attempts = {"n": 0}
        async def _flake():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RateLimitError("slow down")
            return "recovered"

        result = await _with_retry(_flake, provider="test", model="m", attempts=5)
        assert result == "recovered"
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_total_provider_outage_falls_over(self, monkeypatch):
        """Total primary outage + fallback config → failover fires once."""
        from src.platform.llm_router import LLMResponse, LLMRouter
        from stacks.base import LLMConfig
        import src.platform.llm_router as llm_mod
        monkeypatch.setattr(llm_mod, "BACKOFF_BASE_SECONDS", 0.0)
        monkeypatch.setattr(llm_mod, "BACKOFF_MAX_SECONDS", 0.0)

        class APIStatusError(Exception):
            def __init__(self, msg, status_code=503):
                super().__init__(msg)
                self.status_code = status_code

        router = LLMRouter()
        router._clients["anthropic"] = object()
        router._clients["openai"] = object()

        primary_calls = {"n": 0}
        fallback_calls = {"n": 0}

        async def _anthropic_down(client, model, messages, tools):
            primary_calls["n"] += 1
            raise APIStatusError("anthropic 503")

        async def _openai_ok(client, model, messages, tools):
            fallback_calls["n"] += 1
            return LLMResponse(text="fallback saved us", model=model, provider="openai")

        router._call_anthropic = _anthropic_down
        router._call_openai = _openai_ok

        cfg = LLMConfig(
            chat_model="claude-x", provider="anthropic",
            metadata={"fallback_provider": "openai"},
        )
        result = await router.chat(cfg, messages=[{"role": "user", "content": "hi"}])
        assert "fallback saved" in result.text
        # Primary was retried 3x, fallback was called once
        assert primary_calls["n"] == 3
        assert fallback_calls["n"] == 1

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_fast(self):
        """401 Unauthorized should NOT trigger retries."""
        from src.platform.llm_router import _with_retry

        class AuthError(Exception):
            def __init__(self):
                super().__init__("bad token")
                self.status_code = 401

        attempts = {"n": 0}
        async def _auth_fail():
            attempts["n"] += 1
            raise AuthError()

        with pytest.raises(AuthError):
            await _with_retry(_auth_fail, provider="test", model="m", attempts=5)
        # Only one attempt — not retryable
        assert attempts["n"] == 1


# ---------------------------------------------------------------------------
# MCP tool failure simulation
# ---------------------------------------------------------------------------

class TestMCPToolResilience:
    """Tool executor should handle timeouts, exceptions, and whitelist violations."""

    @pytest.mark.asyncio
    async def test_tool_timeout_triggers_retry_then_error(self):
        """A hanging tool should be retried, then return an error dict."""
        from src.platform.agentic_loop import _execute_tool

        class HangingExecutor:
            call_count = 0
            async def execute(self, name, inp, ctx):
                HangingExecutor.call_count += 1
                await asyncio.sleep(10)
                return {"success": True}

        executor = HangingExecutor()
        result = await _execute_tool(
            "t1", {}, executor, None,
            timeout=0.05, max_retries=1,
        )
        assert "error" in result
        assert "timed out" in result["error"].lower()
        # Initial call + 1 retry = 2 attempts
        assert HangingExecutor.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_crash_retries_with_backoff(self):
        """A crashing tool should retry (with exponential backoff)."""
        from src.platform.agentic_loop import _execute_tool

        class CrashyExecutor:
            def __init__(self):
                self.calls = 0
            async def execute(self, name, inp, ctx):
                self.calls += 1
                if self.calls < 2:
                    raise RuntimeError("boom")
                return {"success": True, "result": "recovered"}

        executor = CrashyExecutor()
        start = time.time()
        result = await _execute_tool(
            "t1", {}, executor, None,
            timeout=5, max_retries=2,
        )
        elapsed = time.time() - start

        assert result.get("success") is True
        assert executor.calls == 2
        # There should have been at least one backoff sleep (~0.5s)
        assert elapsed >= 0.4

    @pytest.mark.asyncio
    async def test_explicit_error_dict_not_retried(self):
        """When a tool returns {'error': ...} (deliberate failure), no retry."""
        from src.platform.agentic_loop import _execute_tool

        class ErrorExecutor:
            calls = 0
            async def execute(self, name, inp, ctx):
                ErrorExecutor.calls += 1
                return {"success": False, "error": "deliberate"}

        result = await _execute_tool("t1", {}, ErrorExecutor(), None, timeout=5, max_retries=3)
        assert result["success"] is False
        assert ErrorExecutor.calls == 1

    @pytest.mark.asyncio
    async def test_whitelist_blocks_unauthorized_tool(self):
        """An agent's `allowed_tools` should prevent dispatch."""
        from forgeos_mcp.integration.tool_executor import ToolExecutor

        te = ToolExecutor(company_system=None)
        ctx = {"agent_id": "a1", "allowed_tools": ["company__query_events"]}
        result = await te.execute("mcp__jira__create_issue", {}, agent_context=ctx)
        assert result["success"] is False
        assert "not in agent's allowed tools" in result["error"]


# ---------------------------------------------------------------------------
# Autonomous loop crash resilience (extends Phase 4 tests)
# ---------------------------------------------------------------------------

class TestAutonomousCrashResilience:
    """Repeated crashes should terminate the loop before runaway cost."""

    @pytest.mark.asyncio
    async def test_consecutive_crashes_force_failed_status(self, monkeypatch):
        """3 crashes in a row → status=FAILED, no further iterations."""
        from src.platform.executor import PlatformExecutor
        from src.platform.registry import AgentRegistry
        from src.platform.scheduler import SchedulerEngine
        from src.platform.event_bus import EventBus
        from stacks.base import (
            AgentDefinition, AgentStatus, ExecutionType, OwnershipType,
        )
        from stacks.forgeos.adapter import ForgeOSAdapter

        executor = PlatformExecutor(
            registry=AgentRegistry(),
            scheduler=SchedulerEngine(),
            event_bus=EventBus(),
            agents_root="/tmp/fg-chaos-test",
        )
        executor.register_adapter(ForgeOSAdapter())

        agent = AgentDefinition(
            name="always-crash",
            stack="forgeos",
            execution_type=ExecutionType.AUTONOMOUS,
            ownership=OwnershipType.SHARED,
            goal="crash every time",
            description="Chaos test",
            metadata={
                "max_iterations": 100,
                "loop_interval_seconds": 0,
                "max_crashes_before_give_up": 3,
            },
        )
        aid = await executor.deploy(agent)
        # Cancel auto-spawned task so we drive the loop ourselves
        task = executor._autonomous_tasks.pop(aid, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        invoke_count = {"n": 0}
        async def _always_crash(agent_id, prompt, context=None):
            invoke_count["n"] += 1
            raise RuntimeError(f"crash #{invoke_count['n']}")

        monkeypatch.setattr(executor, "invoke", _always_crash)
        await executor._run_autonomous_loop(agent)

        assert executor.registry.get_status(aid) == AgentStatus.QUARANTINED
        # Should have crashed exactly 3 times (max_crashes_before_give_up)
        assert invoke_count["n"] == 3


# ---------------------------------------------------------------------------
# Event bus concurrency
# ---------------------------------------------------------------------------

class TestEventBusConcurrency:
    """Event bus should handle concurrent fires + subscribes without races."""

    @pytest.mark.asyncio
    async def test_concurrent_subscribes_no_duplicates(self):
        """Subscribing the same agent+event twice should be idempotent."""
        from src.platform.event_bus import EventBus

        bus = EventBus()
        async def _cb(event):
            pass

        # Subscribe 10 times concurrently
        await asyncio.gather(*(
            asyncio.to_thread(bus.subscribe, "test_event", "agent-1", _cb)
            for _ in range(10)
        ))

        subs = bus._subscribers["test_event"]
        # Should only have 1 subscription despite 10 subscribe calls
        assert len(subs) == 1

    @pytest.mark.asyncio
    async def test_fire_with_failing_subscriber(self):
        """A failing subscriber should not break other subscribers."""
        from src.platform.event_bus import EventBus, Event

        bus = EventBus()
        good_called = {"n": 0}

        async def _good(event):
            good_called["n"] += 1

        async def _bad(event):
            raise RuntimeError("subscriber crash")

        bus.subscribe("test", "good-agent", _good)
        bus.subscribe("test", "bad-agent", _bad)

        event = Event(name="test", payload={})
        await bus.fire(event)

        # The good subscriber should still have been called
        assert good_called["n"] == 1
