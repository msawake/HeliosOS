"""
Provider-specific message shaping.

Extracted from ``agentic_loop.py`` so the step engine and the resume path
share one source of truth for how an assistant turn and its tool results are
encoded per provider (Anthropic / OpenAI-compatible / Vertex). The
continuation stores messages *already shaped*, so resume is a pure append:
each pending tool result is injected into the slot keyed by its
``tool_use_id`` immediately after the assistant turn that emitted it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.platform.llm_router import LLMResponse
    from src.runtime.continuation import ToolCallRecord

# Chat-model name prefixes that speak the OpenAI tool-call wire format even
# when the provider string isn't literally "openai" (vLLM/Atlas-served models).
_OPENAI_MODEL_PREFIXES = ("gpt-", "o1-", "o3-", "deepseek-", "qwen-", "nemotron", "nvidia/")
_OPENAI_PROVIDERS = ("openai", "atlas", "vllm")

ANTHROPIC = "anthropic"
OPENAI = "openai"
VERTEX = "vertex"


def provider_kind(provider: str, chat_model: str) -> str:
    """Classify a (provider, chat_model) pair into a shaping dialect."""
    if provider == "vertex":
        return VERTEX
    if provider in _OPENAI_PROVIDERS or chat_model.startswith(_OPENAI_MODEL_PREFIXES):
        return OPENAI
    return ANTHROPIC


def _content_str(result: Any) -> str:
    return json.dumps(result) if isinstance(result, dict) else str(result)


def shape_initial(
    system_prompt: str,
    history: list[dict[str, Any]] | None,
    user_prompt: str,
    context: dict[str, Any] | None,
    goal: str | None,
) -> list[dict[str, Any]]:
    """Build the opening message list (system + history + user).

    The opening shape is provider-agnostic in this codebase — the router
    converts ``{role, content}`` per provider. Only assistant turns and tool
    results are dialect-specific (see :func:`shape_assistant_turn` /
    :func:`shape_tool_results`).
    """
    effective_system = system_prompt
    if goal:
        sanitized_goal = goal[:2000].replace("#", "").replace("```", "")
        effective_system = (
            f"{system_prompt}\n\n"
            f"<agent-goal>\n{sanitized_goal}\n</agent-goal>\n\n"
            f"When you believe the goal inside <agent-goal> is fully achieved, "
            f"end your response with exactly [GOAL_COMPLETE] on its own line. "
            f"If you need more iterations to reach the goal, do NOT include this marker."
        )

    messages: list[dict[str, Any]] = []
    if effective_system:
        messages.append({"role": "system", "content": effective_system})
    if history:
        messages.extend(
            {"role": m["role"], "content": m["content"]}
            for m in history
            if "role" in m and "content" in m
        )
    user_content = user_prompt
    if context:
        user_content += f"\n\nContext: {json.dumps(context, default=str)}"
    messages.append({"role": "user", "content": user_content})
    return messages


def shape_assistant_turn(
    messages: list[dict[str, Any]],
    response: "LLMResponse",
    kind: str,
) -> None:
    """Append the assistant turn that emitted ``response.tool_calls``.

    The appended turn carries the native tool-call ids so the matching
    tool-result message (built later, possibly after a suspend) lines up.
    """
    tool_calls = response.tool_calls or []
    if kind == VERTEX:
        parts: list[dict[str, Any]] = []
        if response.text:
            parts.append({"text": response.text})
        for tc in tool_calls:
            parts.append({"functionCall": {"name": tc.name, "args": tc.input}})
        messages.append({"role": "model", "parts": parts or [{"text": ""}]})
        return

    if kind == OPENAI:
        content_parts: list[str] = []
        if response.reasoning:
            content_parts.append(f"<think>\n{response.reasoning}\n</think>")
        if response.text:
            content_parts.append(response.text)
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": "\n\n".join(content_parts) if content_parts else None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                }
                for tc in tool_calls
            ],
        }
        if response.reasoning:
            assistant_msg["reasoning_content"] = response.reasoning
        messages.append(assistant_msg)
        return

    # Anthropic
    content: list[dict[str, Any]] = []
    if response.text:
        content.append({"type": "text", "text": response.text})
    for tc in tool_calls:
        content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
    messages.append({"role": "assistant", "content": content})


def shape_tool_results(
    messages: list[dict[str, Any]],
    records: list["ToolCallRecord"],
    kind: str,
) -> None:
    """Append tool results for ``records`` into the slot keyed by tool_use_id.

    Each record must have ``result`` populated (and ``result_is_error`` set).
    Order is preserved to match the assistant turn — important for Vertex,
    which keys responses by name+position rather than by id.
    """
    if kind == VERTEX:
        parts = [
            {
                "functionResponse": {
                    "name": rec.name,
                    "response": {"content": _content_str(rec.result)},
                }
            }
            for rec in records
        ]
        messages.append({"role": "user", "parts": parts})
        return

    if kind == OPENAI:
        # OpenAI is strict: one `tool` message per tool_call in the assistant
        # turn. That is exactly why the engine resumes only once *every*
        # pending call is resolved.
        for rec in records:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": rec.tool_use_id,
                    "content": _content_str(rec.result),
                }
            )
        return

    # Anthropic
    blocks = [
        {
            "type": "tool_result",
            "tool_use_id": rec.tool_use_id,
            "content": _content_str(rec.result),
            "is_error": rec.result_is_error,
        }
        for rec in records
    ]
    messages.append({"role": "user", "content": blocks})


__all__ = [
    "ANTHROPIC",
    "OPENAI",
    "VERTEX",
    "provider_kind",
    "shape_assistant_turn",
    "shape_initial",
    "shape_tool_results",
]
