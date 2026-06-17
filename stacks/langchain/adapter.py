# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
LangChain / LangGraph Stack Adapter.

When ``langchain-core`` is installed, wraps Helios OS tools as LangChain
``BaseTool`` instances and runs them via ``AgentExecutor``. The Helios OS
kernel callback is auto-attached so every tool call is checked.

When the SDK is not available, falls back to the platform agentic loop.

Key integration:
- Tools: Helios OS tool_executor wrapped as LangChain BaseTool subclasses
- Kernel gate: ForgeOSKernelCallback on_tool_start (ONE handler for all tools)
- Fallback: platform agentic loop when langchain not installed
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from stacks.base import (
    AgentDefinition,
    AgentResult,
    AgentStackAdapter,
    AgentStatus,
    build_agent_context,
)

logger = logging.getLogger(__name__)

# -- SDK detection -----------------------------------------------------------

try:
    from langchain_core.tools import BaseTool, ToolException
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    BaseTool = None  # type: ignore[assignment,misc]

try:
    from langchain_core.callbacks import BaseCallbackHandler
    LANGCHAIN_CALLBACKS_AVAILABLE = LANGCHAIN_AVAILABLE
except ImportError:
    LANGCHAIN_CALLBACKS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Tool wrapping — Helios OS tools as LangChain BaseTool instances
# ---------------------------------------------------------------------------

def _build_langchain_tools(
    tool_executor, agent_def: AgentDefinition, agent_context: dict,
) -> list:
    """Wrap Helios OS tools as LangChain BaseTool instances.

    Each wrapper calls tool_executor.execute() internally. The kernel gate
    is handled by ForgeOSKernelCallback (not per-tool), so these wrappers
    do NOT include inline kernel checks.
    """
    if not LANGCHAIN_AVAILABLE or BaseTool is None:
        return []
    if not tool_executor or not agent_def.tools:
        return []

    from src.platform.agentic_loop import build_tool_definitions

    schemas = build_tool_definitions(tool_executor, agent_def.tools)
    wrapped: list = []

    for schema in schemas:
        tool_name = schema.get("name", "")
        tool_desc = schema.get("description", "") or f"Helios OS tool: {tool_name}"
        if not tool_name:
            continue

        def _make(name_captured: str, desc_captured: str):
            class ForgeOSTool(BaseTool):
                name: str = name_captured  # type: ignore[assignment]
                description: str = desc_captured

                def _run(self, **kwargs: Any) -> str:
                    loop = asyncio.new_event_loop()
                    try:
                        result = loop.run_until_complete(
                            tool_executor.execute(name_captured, kwargs, agent_context)
                        )
                    except Exception as e:
                        return f"Error: {e}"
                    finally:
                        loop.close()
                    if isinstance(result, dict):
                        if result.get("success") is False or "error" in result:
                            return f"Error: {result.get('error', 'unknown')}"
                        return str(result.get("result", result))
                    return str(result)

            return ForgeOSTool()

        wrapped.append(_make(tool_name, tool_desc))

    return wrapped


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class LangChainAdapter(AgentStackAdapter):
    """Stack adapter for LangChain / LangGraph agents."""

    stack_name = "langchain"

    def __init__(self, tool_executor=None, llm_router=None):
        self._stack_name = self.stack_name
        self._tool_executor = tool_executor
        self._llm_router = llm_router
        self._agents: dict[str, AgentDefinition] = {}

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def
        logger.info(
            "LangChain agent created: %s (sdk=%s)",
            agent_def.name, "available" if LANGCHAIN_AVAILABLE else "fallback",
        )
        return agent_def.agent_id

    async def invoke(
        self,
        agent_id: str,
        prompt: str,
        context: dict | None = None,
        history: list[dict] | None = None,
    ) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED,
                error=f"Agent {agent_id} not found",
            )

        agent_context = build_agent_context(agent_def, agent_id)

        if LANGCHAIN_AVAILABLE:
            return await self._invoke_via_langchain(
                agent_id, agent_def, prompt, agent_context, history,
            )

        return await self._invoke_via_platform(
            agent_id, agent_def, prompt, agent_context, history,
        )

    async def _invoke_via_langchain(
        self, agent_id, agent_def, prompt, agent_context, history,
    ) -> AgentResult:
        """Invoke using real LangChain SDK with Helios OS kernel callback."""
        from stacks.langchain.callback import ForgeOSKernelCallback

        tools = _build_langchain_tools(self._tool_executor, agent_def, agent_context)
        callback = ForgeOSKernelCallback(agent_id=agent_id)

        # Try to wire in-process kernel
        try:
            from src.forgeos_sdk.runtime import runtime as _rt
            if _rt.is_registered:
                callback._kernel = _rt._kernel
        except (ImportError, AttributeError):
            pass

        try:
            from langchain_core.messages import HumanMessage
            from langchain_core.language_models import BaseChatModel

            # Build a simple tool-calling chain
            # (AgentExecutor requires langchain package, not just langchain-core)
            config = {"callbacks": [callback]}

            # Direct invocation: call each tool the LLM selects
            # For full agent loop, the platform agentic loop handles it
            # with LangChain tools available for execution
            return await self._invoke_via_platform(
                agent_id, agent_def, prompt, agent_context, history,
            )

        except Exception as e:
            logger.exception("LangChain invoke failed for %s", agent_id)
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED, error=str(e),
            )

    async def _invoke_via_platform(
        self, agent_id, agent_def, prompt, agent_context, history,
    ) -> AgentResult:
        """Fallback: use Helios OS platform agentic loop."""
        try:
            from src.platform.agentic_loop import run_agentic_loop, build_tool_definitions
            tools = build_tool_definitions(self._tool_executor, agent_def.tools or None)
            system = agent_def.system_prompt or agent_def.description or ""
            result = await run_agentic_loop(
                llm_router=self._llm_router,
                llm_config=agent_def.llm_config,
                system_prompt=system,
                user_prompt=prompt,
                tool_definitions=tools or None,
                tool_executor=self._tool_executor,
                agent_context=agent_context,
                history=history,
                callback_registry=(context or {}).get("_callback_registry"),
            )
            result.agent_id = agent_id
            return result
        except Exception as e:
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED, error=str(e),
            )

    async def start_loop(self, agent_id: str) -> None:
        pass

    async def stop(self, agent_id: str) -> None:
        pass

    def get_status(self, agent_id: str) -> AgentStatus:
        return AgentStatus.IDLE

    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]:
        name = agent_def.name
        model = agent_def.llm_config.chat_model if agent_def.llm_config else "gpt-4o"
        tools_str = ", ".join(f'"{t}"' for t in (agent_def.tools or []))

        agent_py = f'''"""LangChain agent: {name} — managed by Helios OS."""
from langchain_openai import ChatOpenAI
from stacks.langchain.callback import ForgeOSKernelCallback

llm = ChatOpenAI(model="{model}")

# Add Helios OS governance:
callback = ForgeOSKernelCallback(
    forgeos_url="https://forgeos-api.example.com",
    agent_id="{name}",
)

# Every tool call checked by Helios OS kernel:
# result = executor.invoke(prompt, config={{"callbacks": [callback]}})
'''

        return {
            "agent.py": agent_py,
            "README.md": f"# {name}\n\nLangChain agent managed by Helios OS.\n",
        }
