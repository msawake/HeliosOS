"""
Multi-LLM Router.

Routes agent LLM calls to the correct provider and model, supporting
separate chat vs reasoning models per agent. Abstracts away provider
differences so stacks just call `router.chat()` or `router.reason()`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from stacks.base import LLMConfig

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """A tool call requested by the LLM."""
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    tokens_used: int = 0
    finish_reason: str = "stop"
    tool_calls: list[ToolCall] | None = None
    raw: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMRouter:
    """
    Routes LLM calls based on agent LLMConfig.

    Currently returns simulated responses. When real SDKs are plugged in,
    each provider branch calls the actual API client.
    """

    def __init__(self, api_keys: dict[str, str] | None = None):
        self._api_keys = api_keys or {}
        self._clients: dict[str, Any] = {}
        self._init_clients()

    def _init_clients(self) -> None:
        for provider, key in self._api_keys.items():
            if provider == "anthropic" and key:
                try:
                    from anthropic import Anthropic
                    self._clients["anthropic"] = Anthropic(api_key=key)
                    logger.info("Initialized Anthropic client")
                except ImportError:
                    logger.warning("anthropic package not installed")
            elif provider == "openai" and key:
                try:
                    from openai import OpenAI
                    self._clients["openai"] = OpenAI(api_key=key)
                    logger.info("Initialized OpenAI client")
                except ImportError:
                    logger.warning("openai package not installed")
            elif provider == "google" and key:
                logger.info("Google ADK client placeholder registered")

    async def chat(self, llm_config: LLMConfig, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        """Send a chat completion using the agent's chat model."""
        return await self._call(
            provider=llm_config.provider,
            model=llm_config.chat_model,
            messages=messages,
            tools=tools,
        )

    async def reason(self, llm_config: LLMConfig, messages: list[dict]) -> LLMResponse:
        """
        Send a reasoning/thinking call using the agent's reasoning model.
        Falls back to chat model if no reasoning model is configured.
        """
        model = llm_config.reasoning_model or llm_config.chat_model
        return await self._call(
            provider=llm_config.provider,
            model=model,
            messages=messages,
        )

    async def _call(
        self,
        provider: str,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        client = self._clients.get(provider)

        if provider == "anthropic" and client:
            return await self._call_anthropic(client, model, messages, tools)
        if provider == "openai" and client:
            return await self._call_openai(client, model, messages, tools)

        logger.debug(
            "Simulated LLM call: provider=%s model=%s messages=%d",
            provider,
            model,
            len(messages),
        )
        return LLMResponse(
            text=f"[Simulated {provider}/{model}] Processed {len(messages)} message(s).",
            model=model,
            provider=provider,
            tokens_used=0,
        )

    async def _call_anthropic(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None
    ) -> LLMResponse:
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": 4096,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
            response = client.messages.create(**kwargs)
            text = ""
            tool_calls = []
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
                elif block.type == "tool_use":
                    tool_calls.append(ToolCall(
                        id=block.id,
                        name=block.name,
                        input=block.input,
                    ))
            return LLMResponse(
                text=text,
                model=model,
                provider="anthropic",
                tokens_used=response.usage.input_tokens + response.usage.output_tokens,
                finish_reason=response.stop_reason or "stop",
                tool_calls=tool_calls or None,
                raw={"id": response.id, "content": [{"type": b.type} for b in response.content]},
            )
        except Exception as e:
            logger.error("Anthropic API error: %s", e)
            return LLMResponse(text=f"[Error] {e}", model=model, provider="anthropic")

    async def _call_openai(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None
    ) -> LLMResponse:
        try:
            kwargs: dict[str, Any] = {"model": model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
            response = client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            tool_calls = None
            if choice.message.tool_calls:
                import json as _json
                tool_calls = [
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        input=_json.loads(tc.function.arguments) if tc.function.arguments else {},
                    )
                    for tc in choice.message.tool_calls
                ]
            return LLMResponse(
                text=choice.message.content or "",
                model=model,
                provider="openai",
                tokens_used=response.usage.total_tokens if response.usage else 0,
                finish_reason=choice.finish_reason or "stop",
                tool_calls=tool_calls,
            )
        except Exception as e:
            logger.error("OpenAI API error: %s", e)
            return LLMResponse(text=f"[Error] {e}", model=model, provider="openai")

    def available_providers(self) -> list[str]:
        return list(self._clients.keys()) or ["simulated"]
