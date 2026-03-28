"""
Shared agentic tool-use loop for all stack adapters.

Runs the standard LLM → tool_use → tool_result → LLM loop used by the
platform layer. Each stack adapter calls ``run_agentic_loop()`` instead
of manually calling ``llm_router.chat()``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from stacks.base import AgentDefinition, AgentResult, AgentStatus, LLMConfig
from src.platform.llm_router import LLMResponse, LLMRouter

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 25  # safety cap on tool-use iterations


async def run_agentic_loop(
    llm_router: LLMRouter,
    llm_config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    tool_definitions: list[dict] | None = None,
    tool_executor=None,
    agent_context: dict | None = None,
    max_turns: int = MAX_TOOL_TURNS,
    context: dict | None = None,
) -> AgentResult:
    """Run an agentic tool-use loop.

    1. Send system + user messages (with tool definitions) to the LLM.
    2. If the LLM returns tool_use blocks, execute each tool via
       *tool_executor* and append the results as tool_result messages.
    3. Call the LLM again with the updated conversation.
    4. Repeat until the LLM returns ``end_turn`` (no more tool calls)
       or *max_turns* is reached.

    Returns an ``AgentResult`` with the final text output and aggregated
    token count.
    """
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    user_content = user_prompt
    if context:
        user_content += f"\n\nContext: {json.dumps(context)}"
    messages.append({"role": "user", "content": user_content})

    tools = tool_definitions if tool_definitions else None
    total_tokens = 0
    all_tool_calls: list[dict] = []
    final_text = ""

    for turn in range(max_turns):
        response: LLMResponse = await llm_router.chat(llm_config, messages, tools=tools)
        total_tokens += response.tokens_used

        # No tool calls — we're done
        if not response.has_tool_calls:
            final_text = response.text
            break

        # Build assistant message with tool_use content blocks (Anthropic format)
        assistant_content = []
        if response.text:
            assistant_content.append({"type": "text", "text": response.text})
        for tc in response.tool_calls:
            assistant_content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        messages.append({"role": "assistant", "content": assistant_content})

        # Execute each tool and collect results
        tool_results = []
        for tc in response.tool_calls:
            all_tool_calls.append({"name": tc.name, "input": tc.input})
            result_data = await _execute_tool(tc.name, tc.input, tool_executor, agent_context)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": json.dumps(result_data) if isinstance(result_data, dict) else str(result_data),
            })

        messages.append({"role": "user", "content": tool_results})
    else:
        # Exhausted max turns
        final_text = response.text if response else "[Max tool turns reached]"

    return AgentResult(
        agent_id="",  # caller sets this
        status=AgentStatus.COMPLETED,
        output=final_text,
        tool_calls=all_tool_calls,
        tokens_used=total_tokens,
    )


async def _execute_tool(
    tool_name: str,
    tool_input: dict,
    tool_executor,
    agent_context: dict | None,
) -> Any:
    """Execute a single tool call, returning the result dict or error string."""
    if not tool_executor:
        return {"error": f"No tool executor available for tool '{tool_name}'"}
    try:
        if hasattr(tool_executor, "execute"):
            result = await tool_executor.execute(tool_name, tool_input, agent_context)
        else:
            result = {"error": f"Tool executor has no execute method"}
        return result
    except Exception as e:
        logger.exception("Tool execution failed: %s", tool_name)
        return {"error": str(e)}


def build_tool_definitions(tool_executor, agent_tools: list[str] | None = None) -> list[dict]:
    """Collect tool schemas from the tool executor.

    If *agent_tools* is provided, filters to only those tool names.
    Returns the list in Anthropic tool format (name + description + input_schema).
    """
    if not tool_executor:
        return []

    all_tools = []

    # Custom company tools
    if hasattr(tool_executor, "get_custom_tool_definitions"):
        all_tools.extend(tool_executor.get_custom_tool_definitions())

    # MCP tools
    if hasattr(tool_executor, "get_mcp_tool_definitions"):
        all_tools.extend(tool_executor.get_mcp_tool_definitions())
    elif hasattr(tool_executor, "_mcp_tool_definitions"):
        for server_tools in tool_executor._mcp_tool_definitions.values():
            all_tools.extend(server_tools)

    # Platform tools (CRM, HTTP, ads, etc.)
    if hasattr(tool_executor, "get_platform_tool_definitions"):
        all_tools.extend(tool_executor.get_platform_tool_definitions())

    # Filter to agent's allowed tools if specified
    if agent_tools:
        # Allow both exact matches and prefix matches (e.g. "company__" matches all company tools)
        filtered = []
        for tool in all_tools:
            name = tool.get("name", "")
            if name in agent_tools:
                filtered.append(tool)
            elif any(name.startswith(prefix) for prefix in agent_tools if prefix.endswith("*")):
                filtered.append(tool)
        if filtered:
            return filtered

    return all_tools
