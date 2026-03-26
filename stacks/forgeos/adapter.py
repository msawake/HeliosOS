"""
ForgeOS Native Stack Adapter.

Wraps the existing AgentInvoker / hook chain / tool executor into the
AgentStackAdapter interface. This is the built-in "simple" stack that
uses the platform's own LLM client and tool system directly.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
from typing import Any

from stacks.base import (
    AgentDefinition,
    AgentResult,
    AgentStackAdapter,
    AgentStatus,
    ExecutionType,
)

logger = logging.getLogger(__name__)


class ForgeOSAdapter(AgentStackAdapter):
    stack_name = "forgeos"

    def __init__(self, llm_router=None, tool_executor=None):
        self._llm_router = llm_router
        self._tool_executor = tool_executor
        self._agents: dict[str, AgentDefinition] = {}
        self._loops: dict[str, asyncio.Task] = {}

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def
        logger.info("ForgeOS agent created: %s (%s)", agent_def.name, agent_def.agent_id)
        return agent_def.agent_id

    async def invoke(self, agent_id: str, prompt: str, context: dict | None = None) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        if self._llm_router:
            messages = [
                {"role": "system", "content": f"You are {agent_def.name}. {agent_def.description}"},
                {"role": "user", "content": prompt},
            ]
            if context:
                messages[0]["content"] += f"\nContext: {context}"

            response = await self._llm_router.chat(agent_def.llm_config, messages)
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.COMPLETED,
                output=response.text,
                tokens_used=response.tokens_used,
            )

        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.COMPLETED,
            output=f"[ForgeOS simulated] Agent '{agent_def.name}' processed: {prompt[:100]}",
        )

    async def start_loop(self, agent_id: str) -> None:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return

        async def _loop():
            interval = agent_def.metadata.get("loop_interval_seconds", 60)
            while True:
                try:
                    await self.invoke(agent_id, f"Standing duties for {agent_def.name}")
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("ForgeOS loop error for %s", agent_id)
                await asyncio.sleep(interval)

        self._loops[agent_id] = asyncio.create_task(_loop(), name=f"forgeos-loop-{agent_id}")
        logger.info("Started ForgeOS loop for %s", agent_id)

    async def stop(self, agent_id: str) -> None:
        task = self._loops.pop(agent_id, None)
        if task:
            task.cancel()
        logger.info("Stopped ForgeOS agent %s", agent_id)

    def get_status(self, agent_id: str) -> AgentStatus:
        if agent_id in self._loops and not self._loops[agent_id].done():
            return AgentStatus.RUNNING
        if agent_id in self._agents:
            return AgentStatus.IDLE
        return AgentStatus.STOPPED

    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]:
        return {
            "agent.py": textwrap.dedent(f"""\
                \"\"\"
                ForgeOS Agent: {agent_def.name}
                Stack: forgeos | Type: {agent_def.execution_type.value}
                \"\"\"
                from stacks.base import AgentDefinition, ExecutionType, OwnershipType, LLMConfig

                AGENT_DEF = AgentDefinition(
                    name="{agent_def.name}",
                    stack="forgeos",
                    execution_type=ExecutionType.{agent_def.execution_type.name},
                    ownership=OwnershipType.{agent_def.ownership.name},
                    description="{agent_def.description}",
                    tools={agent_def.tools!r},
                    llm_config=LLMConfig(
                        chat_model="{agent_def.llm_config.chat_model}",
                        reasoning_model={agent_def.llm_config.reasoning_model!r},
                        provider="{agent_def.llm_config.provider}",
                    ),
                )
            """),
            "tools.py": textwrap.dedent(f"""\
                \"\"\"MCP-wrapped tools for {agent_def.name}.\"\"\"

                TOOL_DEFINITIONS = {agent_def.tools!r}
            """),
            "prompts/system.md": textwrap.dedent(f"""\
                # {agent_def.name}

                You are {agent_def.name}, a ForgeOS agent.

                ## Role
                {agent_def.description or 'General-purpose assistant.'}

                ## Rules
                - Always think step-by-step
                - Use available MCP tools when needed
                - Report progress clearly
            """),
            "config.yaml": textwrap.dedent(f"""\
                name: "{agent_def.name}"
                stack: forgeos
                execution_type: {agent_def.execution_type.value}
                ownership: {agent_def.ownership.value}
                llm:
                  chat_model: "{agent_def.llm_config.chat_model}"
                  reasoning_model: {agent_def.llm_config.reasoning_model or 'null'}
                  provider: "{agent_def.llm_config.provider}"
                tools: {agent_def.tools!r}
            """),
        }
