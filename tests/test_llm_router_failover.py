"""Tests for LLM router retry and failover logic."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from stacks.base import LLMConfig
from src.platform.llm_router import (
    LLMResponse,
    LLMRouter,
    ToolCall,
    _is_retryable,
    _with_retry,
)


class RateLimitError(Exception):
    """Fake RateLimitError — name-matched by _is_retryable."""


class APIStatusError(Exception):
    def __init__(self, msg: str, status_code: int = 500):
        super().__init__(msg)
        self.status_code = status_code


class FatalAuthError(Exception):
    """Non-retryable for tests."""


class TestIsRetryable:
    def test_rate_limit_retryable(self):
        assert _is_retryable(RateLimitError("slow down"))

    def test_5xx_retryable(self):
        assert _is_retryable(APIStatusError("boom", status_code=503))

    def test_429_retryable(self):
        assert _is_retryable(APIStatusError("rate", status_code=429))

    def test_timeout_message_retryable(self):
        assert _is_retryable(Exception("connection timed out"))

    def test_fatal_not_retryable(self):
        assert not _is_retryable(FatalAuthError("bad api key"))

    def test_400_not_retryable(self):
        assert not _is_retryable(APIStatusError("bad request", status_code=400))


class TestWithRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        async def ok():
            return "result"
        result = await _with_retry(ok, provider="test", model="m")
        assert result == "result"

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self, monkeypatch):
        # Avoid real sleeping
        import src.platform.llm_router as mod
        monkeypatch.setattr(mod, "BACKOFF_BASE_SECONDS", 0.0)
        monkeypatch.setattr(mod, "BACKOFF_MAX_SECONDS", 0.0)

        call_count = {"n": 0}

        async def flaky():
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RateLimitError("slow down")
            return "ok"

        result = await _with_retry(flaky, provider="test", model="m", attempts=5)
        assert result == "ok"
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self):
        call_count = {"n": 0}

        async def bad():
            call_count["n"] += 1
            raise FatalAuthError("auth failed")

        with pytest.raises(FatalAuthError):
            await _with_retry(bad, provider="test", model="m", attempts=5)
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_exhausts_retries_then_raises(self, monkeypatch):
        import src.platform.llm_router as mod
        monkeypatch.setattr(mod, "BACKOFF_BASE_SECONDS", 0.0)
        monkeypatch.setattr(mod, "BACKOFF_MAX_SECONDS", 0.0)

        call_count = {"n": 0}

        async def always_rate_limited():
            call_count["n"] += 1
            raise RateLimitError("always slow")

        with pytest.raises(RateLimitError):
            await _with_retry(always_rate_limited, provider="test", model="m", attempts=3)
        assert call_count["n"] == 3


class TestFailover:
    @pytest.mark.asyncio
    async def test_failover_happy_path(self, monkeypatch):
        """When primary fails and fallback succeeds, return fallback response and audit."""
        import src.platform.llm_router as mod
        monkeypatch.setattr(mod, "BACKOFF_BASE_SECONDS", 0.0)
        monkeypatch.setattr(mod, "BACKOFF_MAX_SECONDS", 0.0)

        router = LLMRouter()
        # Pretend both providers are "available"
        router._clients["anthropic"] = object()
        router._clients["openai"] = object()

        call_log: list[str] = []

        async def fake_anthropic(client, model, messages, tools):
            call_log.append("anthropic")
            raise RateLimitError("primary down")

        async def fake_openai(client, model, messages, tools):
            call_log.append("openai")
            return LLMResponse(text="ok from openai", model=model, provider="openai")

        router._call_anthropic = fake_anthropic
        router._call_openai = fake_openai

        audit_records: list[dict] = []

        class FakeAudit:
            def record(self, action, **kwargs):
                audit_records.append({"action": action, **kwargs})

        router.bind_audit(FakeAudit())

        cfg = LLMConfig(
            chat_model="claude-x",
            provider="anthropic",
            metadata={"fallback_provider": "openai"},
        )
        result = await router.chat(cfg, messages=[{"role": "user", "content": "hi"}])
        assert result.text == "ok from openai"
        assert result.provider == "openai"
        # Primary was retried MAX_RETRIES times, then fallback once
        assert call_log.count("anthropic") >= 1
        assert call_log.count("openai") == 1
        assert any(r["action"] == "platform.llm_failover" for r in audit_records)

    @pytest.mark.asyncio
    async def test_no_fallback_returns_error(self, monkeypatch):
        import src.platform.llm_router as mod
        monkeypatch.setattr(mod, "BACKOFF_BASE_SECONDS", 0.0)
        monkeypatch.setattr(mod, "BACKOFF_MAX_SECONDS", 0.0)

        router = LLMRouter()
        router._clients["anthropic"] = object()

        async def fake_anthropic(client, model, messages, tools):
            raise RateLimitError("boom")
        router._call_anthropic = fake_anthropic

        cfg = LLMConfig(chat_model="claude-x", provider="anthropic")
        result = await router.chat(cfg, messages=[{"role": "user", "content": "hi"}])
        assert "[Error]" in result.text
        assert result.provider == "anthropic"

    @pytest.mark.asyncio
    async def test_simulated_fallback_when_no_clients(self):
        """With no clients configured, return a simulated response (no retries exhausted)."""
        router = LLMRouter()
        cfg = LLMConfig(chat_model="claude-x", provider="anthropic")
        result = await router.chat(cfg, messages=[{"role": "user", "content": "hi"}])
        assert "Simulated" in result.text
