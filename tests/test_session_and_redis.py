"""Tests for session persistence, Redis rate limiter, and checkpointing."""

import time
from unittest.mock import MagicMock

import pytest

from src.core.session_store import AgentSession, InMemorySessionStore
from src.core.redis_rate_limiter import RedisRateLimiter
from src.core.hooks import AgentContext
from src.platform.kernel import KernelDecision


def _make_context(session_id="s1"):
    return AgentContext(
        agent_id="test-agent", agent_type="doer", department="sales",
        tier=3, session_id=session_id,
        allowed_tools=["Read"], budget_tokens=10_000,
        model="claude-sonnet-4-5-20250514",
    )


# ── AgentSession ─────────────────────────────────────────────────────────


class TestAgentSession:
    def test_defaults(self):
        s = AgentSession()
        assert s.status == "running"
        assert s.messages == []
        assert s.tool_calls_completed == 0
        assert s.input_tokens == 0
        assert s.session_id  # UUID generated

    def test_custom_fields(self):
        s = AgentSession(
            agent_id="sales-sdr",
            model="claude-sonnet-4-5-20250514",
            workflow_id="wf-123",
        )
        assert s.agent_id == "sales-sdr"
        assert s.workflow_id == "wf-123"


# ── InMemorySessionStore ────────────────────────────────────────────────


class TestInMemorySessionStore:
    def test_save_and_get(self):
        store = InMemorySessionStore()
        session = AgentSession(agent_id="test")
        store.save(session)
        assert store.get(session.session_id) is session

    def test_get_missing(self):
        store = InMemorySessionStore()
        assert store.get("nonexistent") is None

    def test_update(self):
        store = InMemorySessionStore()
        session = AgentSession(agent_id="test")
        store.save(session)

        session.status = "completed"
        session.input_tokens = 5000
        store.update(session)

        retrieved = store.get(session.session_id)
        assert retrieved.status == "completed"
        assert retrieved.input_tokens == 5000

    def test_list_active(self):
        store = InMemorySessionStore()
        s1 = AgentSession(agent_id="a1", status="running")
        s2 = AgentSession(agent_id="a2", status="running")
        s3 = AgentSession(agent_id="a1", status="completed")
        store.save(s1)
        store.save(s2)
        store.save(s3)

        active = store.list_active()
        assert len(active) == 2

        active_a1 = store.list_active(agent_id="a1")
        assert len(active_a1) == 1

    def test_list_by_workflow(self):
        store = InMemorySessionStore()
        s1 = AgentSession(agent_id="a1", workflow_id="wf-1")
        s2 = AgentSession(agent_id="a2", workflow_id="wf-1")
        s3 = AgentSession(agent_id="a3", workflow_id="wf-2")
        store.save(s1)
        store.save(s2)
        store.save(s3)

        wf1 = store.list_by_workflow("wf-1")
        assert len(wf1) == 2

    def test_checkpoint_data(self):
        store = InMemorySessionStore()
        session = AgentSession(agent_id="test")
        session.checkpoint_data = {"last_tool": "WebSearch", "progress": 0.5}
        session.messages = [{"role": "user", "content": "test"}]
        store.save(session)

        retrieved = store.get(session.session_id)
        assert retrieved.checkpoint_data["progress"] == 0.5
        assert len(retrieved.messages) == 1


# ── RedisRateLimiter (without Redis) ─────────────────────────────────────


class TestRedisRateLimiterFallback:
    def test_no_redis_allows_all(self):
        rl = RedisRateLimiter(redis_url="", max_calls_per_session=5)
        assert not rl.is_distributed
        ctx = _make_context()
        result = rl.check(ctx)
        assert result.action == "allow"

    def test_invalid_url_falls_back(self):
        rl = RedisRateLimiter(redis_url="redis://invalid:99999")
        assert not rl.is_distributed

    def test_get_usage_no_redis(self):
        rl = RedisRateLimiter()
        usage = rl.get_session_usage("s1")
        assert usage == {"total": 0, "per_minute": 0}

    def test_reset_no_redis(self):
        rl = RedisRateLimiter()
        rl.reset_session("s1")  # Should not raise


# ── ClaudeClient Session Integration ────────────────────────────────────


class TestClaudeClientSessions:
    def test_session_store_wired(self):
        from src.core.claude_client import ClaudeClient
        store = InMemorySessionStore()
        client = ClaudeClient(session_store=store)
        assert client._session_store is store

    def test_session_store_optional(self):
        from src.core.claude_client import ClaudeClient
        client = ClaudeClient()
        assert client._session_store is None

    def test_simulate_no_session(self):
        """Simulation mode should not require session store."""
        from src.core.claude_client import ClaudeClient
        store = InMemorySessionStore()
        client = ClaudeClient(session_store=store)
        # Force simulation by clearing the auto-created LLM client
        client._llm_client = None
        import asyncio
        result = asyncio.run(client.run(
            system_prompt="test", prompt="hello", model="test",
        ))
        assert result["status"] == "completed"


# ── Hook Chain Factory ──────────────────────────────────────────────────


class TestHookChainFactory:
    def test_no_redis_uses_in_memory(self):
        from src.core.hooks import create_hook_chain, RateLimiter
        chain = create_hook_chain()
        assert isinstance(chain.rate_limiter, RateLimiter)

    def test_invalid_redis_falls_back(self):
        from src.core.hooks import create_hook_chain, RateLimiter
        chain = create_hook_chain(redis_url="redis://invalid:99999")
        assert isinstance(chain.rate_limiter, RateLimiter)
