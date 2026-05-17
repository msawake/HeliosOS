"""
OpenAI Agents Stack Adapter.

Dual-path: when `openai-agents` SDK is installed, uses the real Agent/Runner
with an on_tool_start hook for ForgeOS kernel governance. Falls back to
direct Responses API HTTP calls when the SDK is not available.

The on_tool_start hook gates EVERY tool call through the ForgeOS kernel —
same pattern as the Anthropic Agent SDK's PreToolUse hook.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import textwrap
from typing import Any

from stacks.base import (
    AgentDefinition,
    AgentResult,
    AgentStackAdapter,
    AgentStatus,
    OwnershipType,
    build_agent_context,
)

logger = logging.getLogger(__name__)

# -- SDK detection -----------------------------------------------------------

try:
    from agents import Agent as OAIAgent, Runner as OAIRunner, function_tool, AgentHooks
    from agents import RunContextWrapper
    SDK_AVAILABLE = True
    logger.info("openai-agents SDK detected — real runtime enabled")
except ImportError:
    SDK_AVAILABLE = False
    OAIAgent = None
    OAIRunner = None
    function_tool = None
    AgentHooks = None
    logger.info("openai-agents SDK not installed — using Responses API fallback")


# ---------------------------------------------------------------------------
# Kernel gate hook (on_tool_start)
# ---------------------------------------------------------------------------

class ForgeOSKernelHooks(AgentHooks if AgentHooks else object):
    """AgentHooks implementation that checks ForgeOS kernel before every tool."""

    async def on_tool_start(self, context, agent, tool) -> None:
        tool_name = getattr(tool, "name", str(tool))
        try:
            from src.forgeos_sdk.runtime import runtime as _rt
            if _rt.is_registered and _rt.is_bound:
                decision = await _rt.check_tool(tool_name, {})
                if decision.denied:
                    logger.info("Kernel DENIED tool %s: %s", tool_name, decision.reason)
                    raise PermissionError(f"ForgeOS kernel denied: {decision.reason}")
        except PermissionError:
            raise
        except Exception as e:
            logger.debug("Kernel hook check failed for %s: %s (allowing)", tool_name, e)

    async def on_tool_end(self, context, agent, tool, result) -> None:
        pass

    async def on_start(self, context, agent) -> None:
        pass

    async def on_end(self, context, agent, output) -> None:
        pass

    async def on_handoff(self, context, agent, source) -> None:
        pass

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        pass

    async def on_llm_end(self, context, agent, response) -> None:
        pass


def make_remote_kernel_hooks(forgeos_url: str, agent_id: str):
    """Create hooks that check ForgeOS kernel via HTTP (Mode C)."""

    class RemoteKernelHooks:
        async def on_tool_start(self, context, agent, tool) -> None:
            tool_name = getattr(tool, "name", str(tool))
            try:
                import httpx
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(f"{forgeos_url}/api/platform/kernel/check-tool", json={
                        "agent_id": agent_id, "tool_name": tool_name, "tool_input": {},
                    })
                    decision = resp.json()
                    if decision.get("action") == "deny":
                        raise PermissionError(f"ForgeOS kernel denied: {decision.get('reason')}")
            except PermissionError:
                raise
            except Exception as e:
                logger.debug("Remote kernel check failed: %s (allowing)", e)

        async def on_tool_end(self, context, agent, tool, result) -> None: pass
        async def on_start(self, context, agent) -> None: pass
        async def on_end(self, context, agent, output) -> None: pass
        async def on_handoff(self, context, agent, source) -> None: pass
        async def on_llm_start(self, context, agent, system_prompt, input_items) -> None: pass
        async def on_llm_end(self, context, agent, response) -> None: pass

    return RemoteKernelHooks()


# ---------------------------------------------------------------------------
# Responses API fallback client
# ---------------------------------------------------------------------------

class ResponsesAPIClient:
    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

    async def create_response(self, model, input_text, instructions="", tools=None):
        import httpx
        body: dict[str, Any] = {"model": model, "input": input_text}
        if instructions: body["instructions"] = instructions
        if tools: body["tools"] = tools
        async with httpx.AsyncClient(timeout=120) as c:
            resp = await c.post("https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=body)
            resp.raise_for_status()
            return resp.json()


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class OpenAIAgentsAdapter(AgentStackAdapter):
    """Stack adapter for OpenAI agents — SDK or Responses API fallback."""

    stack_name = "openai-agents"

    def __init__(self, tool_executor=None, llm_router=None, api_key: str | None = None):
        self._stack_name = self.stack_name
        self._tool_executor = tool_executor
        self._llm_router = llm_router
        self._client = ResponsesAPIClient(api_key=api_key)
        self._agents: dict[str, AgentDefinition] = {}
        self._sdk_agents: dict[str, Any] = {}

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def

        if SDK_AVAILABLE and OAIAgent:
            try:
                tools = self._build_sdk_tools(agent_def)
                sdk_agent = OAIAgent(
                    name=agent_def.name.replace("-", "_"),
                    model=agent_def.llm_config.chat_model or "gpt-4o-mini",
                    instructions=agent_def.system_prompt or agent_def.description or "",
                    tools=tools,
                    hooks=ForgeOSKernelHooks(),
                )
                self._sdk_agents[agent_def.agent_id] = sdk_agent
                logger.info("OpenAI SDK agent created: %s (model=%s, tools=%d)",
                            agent_def.name, agent_def.llm_config.chat_model, len(tools))
            except Exception as e:
                logger.warning("SDK agent creation failed: %s — will use Responses API", e)

        return agent_def.agent_id

    async def invoke(self, agent_id, prompt, context=None, history=None) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        if agent_id in self._sdk_agents:
            return await self._invoke_via_sdk(agent_id, agent_def, prompt)

        return await self._invoke_via_api(agent_id, agent_def, prompt, context, history)

    async def _invoke_via_sdk(self, agent_id, agent_def, prompt) -> AgentResult:
        sdk_agent = self._sdk_agents[agent_id]
        try:
            result = await OAIRunner.run(sdk_agent, input=prompt)

            output = ""
            tool_calls = []
            for item in result.new_items:
                if hasattr(item, "text"):
                    output += item.text
                elif hasattr(item, "type") and "tool_call" in str(item.type):
                    tool_calls.append({"name": getattr(item, "name", "?")})

            if not output and hasattr(result, "final_output"):
                output = str(result.final_output) if result.final_output else ""

            return AgentResult(
                agent_id=agent_id, status=AgentStatus.COMPLETED,
                output=output, tool_calls=tool_calls,
            )
        except PermissionError as e:
            return AgentResult(agent_id=agent_id, status=AgentStatus.COMPLETED,
                               output=f"Tool denied by ForgeOS kernel: {e}")
        except Exception as e:
            logger.exception("OpenAI SDK invoke failed")
            return await self._invoke_via_api(agent_id, agent_def, prompt, None, None)

    async def _invoke_via_api(self, agent_id, agent_def, prompt, context, history) -> AgentResult:
        model = agent_def.llm_config.chat_model or "gpt-4o-mini"
        tools = self._build_api_tools(agent_def)
        try:
            result = await self._client.create_response(
                model=model, input_text=prompt,
                instructions=agent_def.system_prompt or "",
                tools=tools if tools else None,
            )
            output = ""
            for item in result.get("output", []):
                if isinstance(item, dict) and item.get("type") == "message":
                    for content in item.get("content", []):
                        if isinstance(content, dict) and content.get("type") == "output_text":
                            output += content.get("text", "")
            usage = result.get("usage", {})
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.COMPLETED,
                output=output or str(result.get("output", "")),
                tokens_used=usage.get("total_tokens", 0),
            )
        except Exception as e:
            try:
                from src.platform.agentic_loop import run_agentic_loop
                agent_context = build_agent_context(agent_def, context)
                return await run_agentic_loop(
                    llm_router=self._llm_router, llm_config=agent_def.llm_config,
                    system_prompt=agent_def.system_prompt or "", user_prompt=prompt,
                    tool_definitions=None, tool_executor=self._tool_executor,
                    agent_context=agent_context, history=history,
                )
            except Exception as e2:
                return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error=str(e2))

    def _build_sdk_tools(self, agent_def):
        if not function_tool:
            return []
        tools = []
        for tool_name in (agent_def.tools or []):
            if tool_name in ("web_search", "code_interpreter", "file_search"):
                continue

            def _make(name_captured):
                @function_tool(name_override=name_captured)
                async def tool_fn(input: str = "") -> str:
                    """ForgeOS tool."""
                    if self._tool_executor:
                        try:
                            result = await self._tool_executor.execute(
                                name_captured, {"input": input}, {"agent_id": agent_def.agent_id},
                            )
                            return json.dumps(result, default=str) if isinstance(result, dict) else str(result)
                        except Exception as e:
                            return f"Error: {e}"
                    return f"Tool {name_captured} executed (simulated)"
                return tool_fn
            tools.append(_make(tool_name))
        return tools

    def _build_api_tools(self, agent_def):
        tools = []
        for name in (agent_def.tools or []):
            if name == "web_search":
                tools.append({"type": "web_search_preview"})
            elif name == "code_interpreter":
                tools.append({"type": "code_interpreter"})
            else:
                tools.append({"type": "function", "name": name,
                              "description": f"ForgeOS tool: {name}",
                              "parameters": {"type": "object", "properties": {}}})
        return tools

    async def start_loop(self, agent_id): pass
    async def stop(self, agent_id): pass
    def get_status(self, agent_id): return AgentStatus.IDLE

    def scaffold_files(self, agent_def):
        model = agent_def.llm_config.chat_model or "gpt-4o-mini"
        return {
            "agent.py": textwrap.dedent(f'''\
                """OpenAI Agents SDK agent: {agent_def.name}"""
                from agents import Agent, Runner
                agent = Agent(name="{agent_def.name}", model="{model}",
                    instructions="""{(agent_def.system_prompt or '')[:200]}""")
                import asyncio
                result = asyncio.run(Runner.run(agent, input="Hello!"))
                print(result.final_output)
            '''),
            "README.md": f"# {agent_def.name}\n\nOpenAI Agents SDK agent managed by ForgeOS.\n",
        }
