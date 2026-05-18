"""Tests for LLM router streaming (chat_stream)."""

from __future__ import annotations

import pytest

from stacks.base import LLMConfig
from src.platform.llm_router import LLMRouter


class TestChatStreamSimulated:
    """When no provider client is configured, chat_stream emits a simulated
    text_delta + done pair."""

    @pytest.mark.asyncio
    async def test_simulated_stream_yields_events(self):
        router = LLMRouter()
        cfg = LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic")
        messages = [{"role": "user", "content": "hello"}]

        events = []
        async for ev in router.chat_stream(cfg, messages):
            events.append(ev)

        assert len(events) >= 2
        assert events[0]["type"] == "text_delta"
        assert "Simulated" in events[0]["content"]
        assert events[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_simulated_with_tools(self):
        router = LLMRouter()
        cfg = LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic")
        tool_defs = [{"name": "test_tool", "description": "A test", "input_schema": {}}]

        events = [ev async for ev in router.chat_stream(
            cfg,
            [{"role": "user", "content": "use a tool"}],
            tools=tool_defs,
        )]

        assert any(ev.get("type") == "done" for ev in events)

    @pytest.mark.asyncio
    async def test_done_event_has_required_fields(self):
        router = LLMRouter()
        cfg = LLMConfig(chat_model="gpt-4o", provider="openai")
        events = [ev async for ev in router.chat_stream(cfg, [{"role": "user", "content": "x"}])]
        done = next(ev for ev in events if ev.get("type") == "done")
        assert "tokens_used" in done
        assert "text" in done or done.get("text") == ""
        assert "tool_calls" in done
