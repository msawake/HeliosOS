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
    OwnershipType,
    build_agent_context,
)

logger = logging.getLogger(__name__)

try:
    from crewai import Agent as CrewAgent, Task as CrewTask, Crew
    CREWAI_AVAILABLE = True
    logger.info("CrewAI SDK detected — real runtime enabled")
except ImportError:
    CREWAI_AVAILABLE = False
    logger.info("CrewAI SDK not installed — using simulated adapter")

try:
    from crewai.tools import BaseTool as CrewBaseTool
    CREWAI_TOOLS_AVAILABLE = CREWAI_AVAILABLE
except ImportError:
    CREWAI_TOOLS_AVAILABLE = False


def _crewai_llm_id(llm_config) -> str:
    """Map a ForgeOS LLMConfig to a CrewAI/LiteLLM-compatible model identifier.

    CrewAI uses LiteLLM internally, which accepts either a bare model name
    (`gpt-4o`, `claude-3-5-sonnet-20241022`) or a prefixed form
    (`anthropic/claude-3-5-sonnet-...`). We pass a bare string since
    LiteLLM auto-detects from the name.
    """
    return llm_config.chat_model


def _build_crewai_tools(tool_executor, agent_def, agent_context: dict) -> list:
    """Wrap ForgeOS tools as CrewAI BaseTool instances.

    Each wrapper captures the tool name + schema and, on invocation, runs
    `tool_executor.execute(...)` in a fresh event loop. Since CrewAI's
    `crew.kickoff()` runs in a worker thread (via `run_in_executor`), each
    wrapper executes in that thread without a live asyncio loop, so a new
    loop is safe to create.
    """
    if not CREWAI_TOOLS_AVAILABLE or not tool_executor or not agent_def.tools:
        return []

    from src.platform.agentic_loop import build_tool_definitions

    schemas = build_tool_definitions(tool_executor, agent_def.tools)
    wrapped: list = []

    for schema in schemas:
        tool_name = schema.get("name", "")
        tool_desc = schema.get("description", "") or f"ForgeOS tool: {tool_name}"
        if not tool_name:
            continue

        def _make_wrapper(name_captured: str, desc_captured: str):
            """Factory to capture name/desc per tool (closure trap otherwise)."""

            class ForgeOSTool(CrewBaseTool):
                name: str = name_captured
                description: str = desc_captured

                def _run(self, **kwargs) -> str:
                    import asyncio as _asyncio
                    try:
                        loop = _asyncio.new_event_loop()
                        try:
                            result = loop.run_until_complete(
                                tool_executor.execute(name_captured, kwargs, agent_context)
                            )
                        finally:
                            loop.close()
                    except Exception as e:
                        logger.exception("CrewAI tool wrapper %s failed", name_captured)
                        return f"Error: {e}"
                    if isinstance(result, dict):
                        if result.get("success") is False or "error" in result:
                            return f"Error: {result.get('error', 'unknown')}"
                        payload = result.get("result", result)
                        return str(payload)
                    return str(result)

            return ForgeOSTool()

        wrapped.append(_make_wrapper(tool_name, tool_desc))

    return wrapped


class CrewAIAdapter(AgentStackAdapter):
    stack_name = "crewai"

    def __init__(self, llm_router=None, tool_executor=None):
        self._llm_router = llm_router
        self._tool_executor = tool_executor
        self._agents: dict[str, AgentDefinition] = {}
        self._crew_agents: dict[str, Any] = {}
        self._loops: dict[str, asyncio.Task] = {}

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def

        if CREWAI_AVAILABLE:
            # Build the agent_context CrewAI tools will use when invoked.
            agent_context = build_agent_context(agent_def, agent_def.agent_id)
            tools = _build_crewai_tools(self._tool_executor, agent_def, agent_context)
            llm_id = _crewai_llm_id(agent_def.llm_config)

            kwargs = dict(
                role=agent_def.name,
                goal=agent_def.goal or agent_def.description or "Complete the assigned task",
                backstory=f"You are {agent_def.name}, an expert at your role within the crew.",
                verbose=True,
                allow_delegation=False,
            )
            if tools:
                kwargs["tools"] = tools
            if llm_id:
                kwargs["llm"] = llm_id

            try:
                crew_agent = CrewAgent(**kwargs)
                self._crew_agents[agent_def.agent_id] = crew_agent
                logger.info(
                    "CrewAI real agent created: %s (%s) — %d tools, llm=%s",
                    agent_def.name, agent_def.agent_id, len(tools), llm_id,
                )
            except Exception as e:
                logger.warning(
                    "CrewAI real agent creation failed (%s); will fall back to platform loop: %s",
                    agent_def.name, e,
                )
        else:
            logger.info("CrewAI simulated agent created: %s (%s)", agent_def.name, agent_def.agent_id)

        return agent_def.agent_id

    async def invoke(self, agent_id: str, prompt: str, context: dict | None = None, history: list[dict] | None = None) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        if CREWAI_AVAILABLE and agent_id in self._crew_agents:
            return await self._invoke_real(agent_id, agent_def, prompt, history)

        if self._llm_router:
            from src.platform.agentic_loop import run_agentic_loop, build_tool_definitions
            tools = build_tool_definitions(self._tool_executor, agent_def.tools or None)
            system = (
                f"Role: {agent_def.name}\n"
                f"Goal: {agent_def.goal or agent_def.description}\n"
                f"Backstory: You are an expert crew member.\n"
                f"Use available tools to accomplish your goal."
            )
            result = await run_agentic_loop(
                llm_router=self._llm_router,
                llm_config=agent_def.llm_config,
                system_prompt=system,
                user_prompt=prompt,
                tool_definitions=tools or None,
                tool_executor=self._tool_executor,
                agent_context=build_agent_context(agent_def, agent_id),
                history=history,
            )
            result.agent_id = agent_id
            return result

        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.COMPLETED,
            output=f"[CrewAI simulated] Crew member '{agent_def.name}' processed: {prompt[:100]}",
        )

    async def _invoke_real(
        self, agent_id: str, agent_def: AgentDefinition, prompt: str,
        history: list[dict] | None = None,
    ) -> AgentResult:
        """Invoke via real CrewAI SDK — runs a single-agent Crew with one Task."""
        crew_agent = self._crew_agents[agent_id]

        # Inject conversation history into task description so the crew has context
        task_description = prompt
        if history:
            history_lines = "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in history[-10:]  # last 10 turns
            )
            task_description = f"## Conversation History\n{history_lines}\n\n## Current Request\n{prompt}"

        task = CrewTask(
            description=task_description,
            agent=crew_agent,
            expected_output="Structured result with actionable insights",
        )

        crew = Crew(
            agents=[crew_agent],
            tasks=[task],
            verbose=False,
        )

        try:
            loop = asyncio.get_running_loop()
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
