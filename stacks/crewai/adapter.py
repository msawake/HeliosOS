"""
CrewAI Stack Adapter.

Scaffolds agents in the CrewAI pattern: Agent(role, goal, backstory, tools)
wrapped in Tasks and orchestrated by a Crew. When the `crewai` SDK is
installed, the adapter delegates to the real runtime. Otherwise it simulates
execution through the platform LLM router.
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
)

logger = logging.getLogger(__name__)

try:
    from crewai import Agent as CrewAgent, Task as CrewTask, Crew
    CREWAI_AVAILABLE = True
    logger.info("CrewAI SDK detected — real runtime enabled")
except ImportError:
    CREWAI_AVAILABLE = False
    logger.info("CrewAI SDK not installed — using simulated adapter")


class CrewAIAdapter(AgentStackAdapter):
    stack_name = "crewai"

    def __init__(self, llm_router=None):
        self._llm_router = llm_router
        self._agents: dict[str, AgentDefinition] = {}
        self._crew_agents: dict[str, Any] = {}
        self._loops: dict[str, asyncio.Task] = {}

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def

        if CREWAI_AVAILABLE:
            crew_agent = CrewAgent(
                role=agent_def.name,
                goal=agent_def.goal or agent_def.description or "Complete the assigned task",
                backstory=f"You are {agent_def.name}, an expert at your role within the crew.",
                verbose=True,
                allow_delegation=False,
            )
            self._crew_agents[agent_def.agent_id] = crew_agent
            logger.info("CrewAI real agent created: %s (%s)", agent_def.name, agent_def.agent_id)
        else:
            logger.info("CrewAI simulated agent created: %s (%s)", agent_def.name, agent_def.agent_id)

        return agent_def.agent_id

    async def invoke(self, agent_id: str, prompt: str, context: dict | None = None) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        if CREWAI_AVAILABLE and agent_id in self._crew_agents:
            return await self._invoke_real(agent_id, agent_def, prompt)

        if self._llm_router:
            messages = [
                {
                    "role": "system",
                    "content": (
                        f"Role: {agent_def.name}\n"
                        f"Goal: {agent_def.goal or agent_def.description}\n"
                        f"Backstory: You are an expert crew member."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
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
            output=f"[CrewAI simulated] Crew member '{agent_def.name}' processed: {prompt[:100]}",
        )

    async def _invoke_real(self, agent_id: str, agent_def: AgentDefinition, prompt: str) -> AgentResult:
        """Invoke via real CrewAI SDK — runs a single-agent Crew with one Task."""
        crew_agent = self._crew_agents[agent_id]

        task = CrewTask(
            description=prompt,
            agent=crew_agent,
            expected_output="Structured result with actionable insights",
        )

        crew = Crew(
            agents=[crew_agent],
            tasks=[task],
            verbose=False,
        )

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, crew.kickoff)

            output_text = str(result)
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.COMPLETED,
                output=output_text,
            )
        except Exception as e:
            logger.exception("CrewAI real invocation failed for %s", agent_id)
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.FAILED,
                error=str(e),
            )

    async def start_loop(self, agent_id: str) -> None:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return

        async def _loop():
            interval = agent_def.metadata.get("loop_interval_seconds", 60)
            while True:
                try:
                    await self.invoke(agent_id, f"Crew duties for {agent_def.name}")
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("CrewAI loop error for %s", agent_id)
                await asyncio.sleep(interval)

        self._loops[agent_id] = asyncio.create_task(_loop(), name=f"crewai-loop-{agent_id}")

    async def stop(self, agent_id: str) -> None:
        task = self._loops.pop(agent_id, None)
        if task:
            task.cancel()

    def get_status(self, agent_id: str) -> AgentStatus:
        if agent_id in self._loops and not self._loops[agent_id].done():
            return AgentStatus.RUNNING
        if agent_id in self._agents:
            return AgentStatus.IDLE
        return AgentStatus.STOPPED

    @staticmethod
    def is_sdk_available() -> bool:
        return CREWAI_AVAILABLE

    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]:
        return {
            "agents.py": textwrap.dedent(f"""\
                \"\"\"
                CrewAI Agent Definitions for: {agent_def.name}
                \"\"\"
                {"from crewai import Agent" if CREWAI_AVAILABLE else "# pip install crewai to enable real runtime"}
                {"" if CREWAI_AVAILABLE else "# from crewai import Agent"}

                {"agent = Agent(" if CREWAI_AVAILABLE else "# agent = Agent("}
                {"    role=" + repr(agent_def.name) + "," if CREWAI_AVAILABLE else "#     role=" + repr(agent_def.name) + ","}
                {"    goal=" + repr(agent_def.goal or agent_def.description) + "," if CREWAI_AVAILABLE else "#     goal=" + repr(agent_def.goal or agent_def.description) + ","}
                {"    backstory='You are an expert at your role within the crew.'," if CREWAI_AVAILABLE else "#     backstory='You are an expert at your role within the crew.',"}
                {"    verbose=True," if CREWAI_AVAILABLE else "#     verbose=True,"}
                {")" if CREWAI_AVAILABLE else "# )"}

                AGENT_CONFIG = {{
                    "role": "{agent_def.name}",
                    "goal": "{agent_def.goal or agent_def.description}",
                    "backstory": "You are an expert at your role within the crew.",
                    "llm": "{agent_def.llm_config.chat_model}",
                    "tools": {agent_def.tools!r},
                    "verbose": True,
                }}
            """),
            "tasks.py": textwrap.dedent(f"""\
                \"\"\"
                CrewAI Task Definitions for: {agent_def.name}
                \"\"\"
                {"from crewai import Task" if CREWAI_AVAILABLE else "# pip install crewai to enable"}

                TASK_CONFIG = {{
                    "description": "{agent_def.description or 'Execute the primary objective'}",
                    "expected_output": "Structured result with actionable insights",
                }}
            """),
            "crew.py": textwrap.dedent(f"""\
                \"\"\"
                CrewAI Crew Orchestrator for: {agent_def.name}
                \"\"\"
                {"from crewai import Crew" if CREWAI_AVAILABLE else "# pip install crewai to enable"}

                CREW_CONFIG = {{
                    "process": "sequential",
                    "memory": True,
                    "verbose": 2,
                }}

                {"# To run: crew = Crew(agents=[agent], tasks=[task], **CREW_CONFIG)" if not CREWAI_AVAILABLE else ""}
                {"# result = crew.kickoff()" if not CREWAI_AVAILABLE else ""}
            """),
            "tools.py": textwrap.dedent(f"""\
                \"\"\"MCP-wrapped tools for CrewAI agent: {agent_def.name}\"\"\"

                TOOL_DEFINITIONS = {agent_def.tools!r}
            """),
            "config.yaml": textwrap.dedent(f"""\
                name: "{agent_def.name}"
                stack: crewai
                sdk_available: {"true" if CREWAI_AVAILABLE else "false"}
                execution_type: {agent_def.execution_type.value}
                ownership: {agent_def.ownership.value}
                crew:
                  process: sequential
                  memory: true
                llm:
                  chat_model: "{agent_def.llm_config.chat_model}"
                  provider: "{agent_def.llm_config.provider}"
                tools: {agent_def.tools!r}
            """),
        }
