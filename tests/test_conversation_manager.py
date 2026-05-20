"""Tests for conversation managers."""
import pytest
from src.platform.conversation_manager import (
    ConversationManager, NullManager, SlidingWindowManager, SummarizingManager,
)


class TestNullManager:
    def test_returns_unchanged(self):
        mgr = NullManager()
        msgs = [{"role": "user", "content": "hello"}]
        assert mgr.reduce(msgs) is msgs

    def test_empty(self):
        mgr = NullManager()
        assert mgr.reduce([]) == []


class TestSlidingWindowManager:
    def test_under_limit_unchanged(self):
        mgr = SlidingWindowManager(window_size=10)
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(5)]
        result = mgr.reduce(msgs)
        assert len(result) == 5

    def test_over_limit_trims(self):
        mgr = SlidingWindowManager(window_size=3)
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        result = mgr.reduce(msgs)
        assert len(result) == 3
        # Should keep last 3
        assert result[0]["content"] == "msg7"
        assert result[2]["content"] == "msg9"

    def test_preserves_system_prompt(self):
        mgr = SlidingWindowManager(window_size=2)
        msgs = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
            {"role": "user", "content": "msg3"},
        ]
        result = mgr.reduce(msgs)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful"
        assert len(result) == 3  # system + last 2 messages

    def test_max_messages_override(self):
        mgr = SlidingWindowManager(window_size=10)
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(20)]
        result = mgr.reduce(msgs, max_messages=5)
        assert len(result) == 5

    def test_empty(self):
        mgr = SlidingWindowManager(window_size=5)
        assert mgr.reduce([]) == []


class TestSummarizingManager:
    def test_under_threshold_unchanged(self):
        mgr = SummarizingManager(summary_threshold=30)
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        result = mgr.reduce(msgs)
        assert len(result) == 10

    def test_over_threshold_summarizes(self):
        mgr = SummarizingManager(summary_threshold=10, keep_recent=3)
        msgs = [
            {"role": "system", "content": "System prompt"},
        ] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}"}
            for i in range(20)
        ]
        result = mgr.reduce(msgs)
        # Should have: system + summary + 3 recent
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "System prompt"
        assert "summary" in result[1]["content"].lower()
        assert len(result) == 5  # system + summary + 3 recent

    def test_custom_summarize_fn(self):
        custom_summary = lambda msgs: "Custom summary of conversation"
        mgr = SummarizingManager(summary_threshold=5, keep_recent=2, summarize_fn=custom_summary)
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        result = mgr.reduce(msgs)
        assert any("Custom summary" in m.get("content", "") for m in result)

    def test_summarize_fn_failure_falls_back(self):
        def bad_fn(msgs):
            raise RuntimeError("LLM unavailable")
        mgr = SummarizingManager(summary_threshold=5, keep_recent=2, summarize_fn=bad_fn)
        msgs = [
            {"role": "user", "content": "question 1"},
            {"role": "assistant", "content": "answer 1"},
            {"role": "user", "content": "question 2"},
            {"role": "assistant", "content": "answer 2"},
            {"role": "user", "content": "question 3"},
            {"role": "assistant", "content": "answer 3"},
            {"role": "user", "content": "recent 1"},
            {"role": "assistant", "content": "recent 2"},
        ]
        result = mgr.reduce(msgs)
        # Should still work with fallback summary
        assert len(result) < len(msgs)

    def test_empty(self):
        mgr = SummarizingManager()
        assert mgr.reduce([]) == []


class TestProtocol:
    def test_null_satisfies_protocol(self):
        assert isinstance(NullManager(), ConversationManager)

    def test_sliding_satisfies_protocol(self):
        assert isinstance(SlidingWindowManager(), ConversationManager)

    def test_summarizing_satisfies_protocol(self):
        assert isinstance(SummarizingManager(), ConversationManager)
