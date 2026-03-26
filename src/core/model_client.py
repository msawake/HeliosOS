"""
Multi-model LLM client abstraction.

Provides a provider-agnostic interface for making LLM API calls.
Supports Anthropic (Claude) and OpenAI (GPT/o-series) with graceful
fallback when SDKs are not installed.

The agentic loop in claude_client.py uses this interface so agents
can run on any supported model.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider-agnostic response types
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A single tool call requested by the model."""
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    """Provider-agnostic response from an LLM API call."""
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens"
    input_tokens: int
    output_tokens: int
    raw_response: Any = None


# ---------------------------------------------------------------------------
# LLM Client Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMClient(Protocol):
    """Interface that each model provider must implement."""

    def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> LLMResponse: ...

    def format_tool_result(
        self,
        tool_call_id: str,
        content: str,
        is_error: bool = False,
    ) -> dict: ...

    def format_assistant_message(self, response: LLMResponse) -> dict: ...


# ---------------------------------------------------------------------------
# Provider enum and detection
# ---------------------------------------------------------------------------

class ModelProvider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


def get_provider(model_name: str) -> ModelProvider:
    """Detect the provider from a model name string."""
    name = model_name.lower()
    if name.startswith("claude-") or name.startswith("anthropic/"):
        return ModelProvider.ANTHROPIC
    if (
        name.startswith("gpt-")
        or name.startswith("o1-")
        or name.startswith("o3-")
        or name.startswith("o4-")
        or name.startswith("openai/")
    ):
        return ModelProvider.OPENAI
    raise ValueError(
        f"Unknown model provider for '{model_name}'. "
        f"Expected prefix: claude-*, gpt-*, o1-*, o3-*, o4-*"
    )


# ---------------------------------------------------------------------------
# Pricing registry (single source of truth)
# ---------------------------------------------------------------------------

# Prices per million tokens (USD)
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-5-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.0, "output": 8.0},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "o4-mini": {"input": 1.10, "output": 4.40},
}

# Fallback pricing for unknown models
_DEFAULT_PRICING = {"input": 3.0, "output": 15.0}


def register_pricing(model: str, input_per_million: float, output_per_million: float) -> None:
    """Register pricing for a custom or new model."""
    MODEL_PRICING[model] = {"input": input_per_million, "output": output_per_million}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a given model and token counts."""
    rates = MODEL_PRICING.get(model, _DEFAULT_PRICING)
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Anthropic Client
# ---------------------------------------------------------------------------

try:
    import anthropic as _anthropic_sdk
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


class AnthropicClient:
    """LLMClient implementation for the Anthropic (Claude) API."""

    def __init__(self, api_key: str | None = None):
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package is required: pip install anthropic")
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        self._client = _anthropic_sdk.Anthropic(**kwargs)

    def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        api_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            api_kwargs["tools"] = tools

        response = self._client.messages.create(**api_kwargs)

        # Parse response
        text = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        stop_reason = "end_turn" if response.stop_reason == "end_turn" else "tool_use"

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            raw_response=response,
        )

    def format_tool_result(
        self,
        tool_call_id: str,
        content: str,
        is_error: bool = False,
    ) -> dict:
        return {
            "type": "tool_result",
            "tool_use_id": tool_call_id,
            "content": content,
            "is_error": is_error,
        }

    def format_assistant_message(self, response: LLMResponse) -> dict:
        """Format the raw response as an assistant message for the conversation."""
        return {"role": "assistant", "content": response.raw_response.content}


# ---------------------------------------------------------------------------
# OpenAI Client
# ---------------------------------------------------------------------------

try:
    import openai as _openai_sdk
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


class OpenAIClient:
    """LLMClient implementation for the OpenAI API (GPT, o-series)."""

    def __init__(self, api_key: str | None = None):
        if not HAS_OPENAI:
            raise ImportError("openai package is required: pip install openai")
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        self._client = _openai_sdk.OpenAI(**kwargs)

    def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 8192,
    ) -> LLMResponse:
        # OpenAI puts system prompt in messages
        oai_messages = [{"role": "system", "content": system}]
        for msg in messages:
            oai_messages.append(self._convert_message(msg))

        api_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": oai_messages,
        }
        if tools:
            api_kwargs["tools"] = [self._convert_tool(t) for t in tools]

        response = self._client.chat.completions.create(**api_kwargs)

        choice = response.choices[0]
        text = choice.message.content or ""
        tool_calls = []

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=json.loads(tc.function.arguments) if tc.function.arguments else {},
                ))

        # Normalize stop reason
        stop_reason = "end_turn"
        if choice.finish_reason == "tool_calls":
            stop_reason = "tool_use"
        elif choice.finish_reason == "length":
            stop_reason = "max_tokens"

        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            raw_response=response,
        )

    def format_tool_result(
        self,
        tool_call_id: str,
        content: str,
        is_error: bool = False,
    ) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }

    def format_assistant_message(self, response: LLMResponse) -> dict:
        """Format the raw response as an assistant message for the conversation."""
        choice = response.raw_response.choices[0]
        msg: dict[str, Any] = {"role": "assistant", "content": response.text}
        if choice.message.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.message.tool_calls
            ]
        return msg

    @staticmethod
    def _convert_tool(tool_def: dict) -> dict:
        """Convert Anthropic-style tool definition to OpenAI format."""
        return {
            "type": "function",
            "function": {
                "name": tool_def["name"],
                "description": tool_def.get("description", ""),
                "parameters": tool_def.get("input_schema", {"type": "object", "properties": {}}),
            },
        }

    @staticmethod
    def _convert_message(msg: dict) -> dict:
        """Convert message format between providers."""
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Handle Anthropic-style tool_result content blocks
        if role == "user" and isinstance(content, list):
            # Check if these are tool results
            tool_results = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": item.get("tool_use_id", ""),
                        "content": item.get("content", ""),
                    })
            if tool_results:
                # Return first one; OpenAI wants separate messages per tool result
                # The agentic loop handles this correctly via format_tool_result
                return tool_results[0] if len(tool_results) == 1 else tool_results[0]

        return {"role": role, "content": content if isinstance(content, str) else json.dumps(content)}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_llm_client(
    model: str,
    api_key: str | None = None,
) -> LLMClient | None:
    """Create the appropriate LLM client based on model name.

    Returns None if the required SDK is not installed.
    """
    try:
        provider = get_provider(model)
    except ValueError:
        logger.warning("Unknown model provider for '%s', trying Anthropic", model)
        provider = ModelProvider.ANTHROPIC

    if provider == ModelProvider.ANTHROPIC:
        if not HAS_ANTHROPIC:
            logger.info("anthropic SDK not installed — cannot create AnthropicClient")
            return None
        try:
            return AnthropicClient(api_key=api_key)
        except Exception as e:
            logger.warning("Failed to create AnthropicClient: %s", e)
            return None

    if provider == ModelProvider.OPENAI:
        if not HAS_OPENAI:
            logger.info("openai SDK not installed — cannot create OpenAIClient")
            return None
        try:
            return OpenAIClient(api_key=api_key)
        except Exception as e:
            logger.warning("Failed to create OpenAIClient: %s", e)
            return None

    return None
