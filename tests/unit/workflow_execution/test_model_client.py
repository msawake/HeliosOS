"""Tests for multi-model LLM client abstraction."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.core.model_client import (
    MODEL_PRICING,
    AnthropicClient,
    LLMResponse,
    ModelProvider,
    ToolCall,
    create_llm_client,
    estimate_cost,
    get_provider,
    register_pricing,
)


# ── LLMResponse / ToolCall ───────────────────────────────────────────────


class TestDataclasses:
    def test_tool_call(self):
        tc = ToolCall(id="tc_1", name="WebSearch", input={"query": "test"})
        assert tc.id == "tc_1"
        assert tc.name == "WebSearch"
        assert tc.input == {"query": "test"}

    def test_llm_response(self):
        r = LLMResponse(
            text="Hello",
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=50,
        )
        assert r.text == "Hello"
        assert r.stop_reason == "end_turn"
        assert r.raw_response is None

    def test_llm_response_with_tool_calls(self):
        tc = ToolCall(id="tc_1", name="Read", input={"path": "/tmp/f"})
        r = LLMResponse(
            text="",
            tool_calls=[tc],
            stop_reason="tool_use",
            input_tokens=200,
            output_tokens=100,
        )
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].name == "Read"


# ── Provider Detection ───────────────────────────────────────────────────


class TestGetProvider:
    def test_claude_models(self):
        assert get_provider("claude-opus-4-6") == ModelProvider.ANTHROPIC
        assert get_provider("claude-sonnet-4-5-20250514") == ModelProvider.ANTHROPIC
        assert get_provider("claude-haiku-4-5-20251001") == ModelProvider.ANTHROPIC

    def test_openai_models(self):
        assert get_provider("gpt-4o") == ModelProvider.OPENAI
        assert get_provider("gpt-4o-mini") == ModelProvider.OPENAI
        assert get_provider("gpt-4.1") == ModelProvider.OPENAI
        assert get_provider("o3-mini") == ModelProvider.OPENAI
        assert get_provider("o4-mini") == ModelProvider.OPENAI

    def test_prefixed_models(self):
        assert get_provider("anthropic/claude-3") == ModelProvider.ANTHROPIC
        assert get_provider("openai/gpt-4o") == ModelProvider.OPENAI

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown model provider"):
            get_provider("llama-3.1-70b")

    def test_case_insensitive(self):
        assert get_provider("Claude-Opus-4-6") == ModelProvider.ANTHROPIC
        assert get_provider("GPT-4o") == ModelProvider.OPENAI


# ── Pricing ──────────────────────────────────────────────────────────────


class TestPricing:
    def test_claude_pricing_exists(self):
        assert "claude-opus-4-6" in MODEL_PRICING
        assert "claude-sonnet-4-5-20250514" in MODEL_PRICING
        assert "claude-haiku-4-5-20251001" in MODEL_PRICING

    def test_openai_pricing_exists(self):
        assert "gpt-4o" in MODEL_PRICING
        assert "gpt-4o-mini" in MODEL_PRICING

    def test_estimate_cost_known(self):
        # claude-sonnet: input=3.0, output=15.0 per million
        cost = estimate_cost("claude-sonnet-4-5-20250514", 1_000_000, 1_000_000)
        assert cost == 3.0 + 15.0

    def test_estimate_cost_unknown_model(self):
        # Should use default pricing (3.0 / 15.0)
        cost = estimate_cost("unknown-model-xyz", 1_000_000, 0)
        assert cost == 3.0  # default input rate

    def test_register_pricing(self):
        register_pricing("custom-model-v1", 2.0, 10.0)
        assert "custom-model-v1" in MODEL_PRICING
        cost = estimate_cost("custom-model-v1", 1_000_000, 1_000_000)
        assert cost == 12.0
        # Cleanup
        del MODEL_PRICING["custom-model-v1"]

    def test_opus_more_expensive_than_haiku(self):
        opus_cost = estimate_cost("claude-opus-4-6", 100_000, 100_000)
        haiku_cost = estimate_cost("claude-haiku-4-5-20251001", 100_000, 100_000)
        assert opus_cost > haiku_cost


# ── AnthropicClient ──────────────────────────────────────────────────────


class TestAnthropicClient:
    def test_format_tool_result(self):
        client = AnthropicClient.__new__(AnthropicClient)
        result = client.format_tool_result("tc_123", '{"ok": true}', is_error=False)
        assert result == {
            "type": "tool_result",
            "tool_use_id": "tc_123",
            "content": '{"ok": true}',
            "is_error": False,
        }

    def test_format_tool_result_error(self):
        client = AnthropicClient.__new__(AnthropicClient)
        result = client.format_tool_result("tc_123", "failed", is_error=True)
        assert result["is_error"] is True
        assert result["type"] == "tool_result"


# ── OpenAIClient ─────────────────────────────────────────────────────────


class TestOpenAIClient:
    def test_format_tool_result(self):
        client = OpenAIClient.__new__(OpenAIClient)
        result = client.format_tool_result("call_abc", '{"ok": true}', is_error=False)
        assert result == {
            "role": "tool",
            "tool_call_id": "call_abc",
            "content": '{"ok": true}',
        }

    def test_convert_tool(self):
        tool_def = {
            "name": "company__search_knowledge",
            "description": "Search the knowledge base",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
        result = OpenAIClient._convert_tool(tool_def)
        assert result["type"] == "function"
        assert result["function"]["name"] == "company__search_knowledge"
        assert result["function"]["parameters"]["type"] == "object"


# Need to import after test definitions to avoid import issues
try:
    from src.core.model_client import OpenAIClient
except ImportError:
    pass


# ── Factory ──────────────────────────────────────────────────────────────


class TestFactory:
    def test_create_anthropic_client(self):
        # anthropic SDK is installed, so this should work
        client = create_llm_client("claude-sonnet-4-5-20250514")
        assert client is not None
        assert isinstance(client, AnthropicClient)

    def test_create_unknown_defaults_to_anthropic(self):
        # Unknown model name falls back to Anthropic
        client = create_llm_client("unknown-model-xyz")
        assert client is not None

    def test_create_openai_without_sdk(self):
        # OpenAI SDK may or may not be installed
        from src.core.model_client import HAS_OPENAI
        client = create_llm_client("gpt-4o")
        if HAS_OPENAI:
            assert client is not None
        else:
            assert client is None
