"""
Google ADK Stack Adapter.

Scaffolds agents in the ADK pattern: Python classes inheriting from
LLMAgent/WorkflowAgent, wired into a hierarchy, executed by a Runner
with state machines. Adapter simulates execution; real `google-adk`
SDK plugged in later.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap

from stacks.base import (
    AgentDefinition,
    AgentResult,
    AgentStackAdapter,
    AgentStatus,
)

logger = logging.getLogger(__name__)


class ADKAdapter(AgentStackAdapter):
    stack_name = "adk"

    def __init__(self, llm_router=None):
        self._llm_router = llm_router
        self._agents: dict[str, AgentDefinition] = {}
        self._loops: dict[str, asyncio.Task] = {}

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def
        logger.info("ADK agent created: %s (%s)", agent_def.name, agent_def.agent_id)
        return agent_def.agent_id

    async def invoke(self, agent_id: str, prompt: str, context: dict | None = None) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        if self._llm_router:
            messages = [
                {"role": "system", "content": f"You are {agent_def.name}, a Google ADK enterprise agent.\n{agent_def.description}"},
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
            output=f"[ADK simulated] Agent '{agent_def.name}' processed: {prompt[:100]}",
        )

    async def start_loop(self, agent_id: str) -> None:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return

        async def _loop():
            interval = agent_def.metadata.get("loop_interval_seconds", 60)
            while True:
                try:
                    await self.invoke(agent_id, f"ADK workflow cycle for {agent_def.name}")
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("ADK loop error for %s", agent_id)
                await asyncio.sleep(interval)

        self._loops[agent_id] = asyncio.create_task(_loop(), name=f"adk-loop-{agent_id}")

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

    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]:
        return {
            "agent.py": textwrap.dedent(f"""\
                \"\"\"
                Google ADK Agent: {agent_def.name}
                Enterprise hierarchical agent using ADK patterns.
                \"\"\"
                # When google-adk SDK is installed:
                # from google.adk import LLMAgent, WorkflowAgent
                #
                # class {_class_name(agent_def.name)}(LLMAgent):
                #     def __init__(self):
                #         super().__init__(
                #             name="{agent_def.name}",
                #             model="gemini-2.0-flash",
                #             system_prompt=open("prompts/system_prompt.txt").read(),
                #             tools=load_mcp_tools(),
                #         )
                #
                #     def run(self, goal: str):
                #         return self.execute(goal)

                AGENT_CONFIG = {{
                    "name": "{agent_def.name}",
                    "model": "{agent_def.llm_config.chat_model}",
                    "tools": {agent_def.tools!r},
                    "description": "{agent_def.description}",
                }}
            """),
            "workflow.py": textwrap.dedent(f"""\
                \"\"\"
                ADK Workflow: Hierarchical agent graph for {agent_def.name}
                \"\"\"
                # When google-adk SDK is installed:
                # from google.adk import SequentialWorkflow
                #
                # workflow = SequentialWorkflow(
                #     name="{agent_def.name} Workflow",
                #     agents=[
                #         ResearcherAgent(),
                #         AnalyzerAgent(),
                #         ReviewerAgent(),   # human gate
                #     ],
                # )

                WORKFLOW_CONFIG = {{
                    "name": "{agent_def.name} Workflow",
                    "type": "sequential",
                    "agents": ["{agent_def.name}"],
                }}
            """),
            "tools.py": textwrap.dedent(f"""\
                \"\"\"MCP-wrapped tools for ADK agent: {agent_def.name}\"\"\"
                # When google-adk SDK is installed:
                # from google.adk import tool
                #
                # @tool
                # def mcp_tool():
                #     \"\"\"Calls MCP gateway\"\"\"
                #     return mcp_client.call("/tool/name")

                TOOL_DEFINITIONS = {agent_def.tools!r}
            """),
            "prompts/system_prompt.txt": textwrap.dedent(f"""\
                You are {agent_def.name}, an enterprise ADK agent.

                Role: {agent_def.description or 'Enterprise assistant'}

                Instructions:
                - Follow the ADK workflow state machine
                - Use approved tools via MCP gateway
                - Escalate to human reviewers for high-risk actions
                - Maintain audit trail of all decisions
            """),
            "config.yaml": textwrap.dedent(f"""\
                name: "{agent_def.name}"
                stack: adk
                execution_type: {agent_def.execution_type.value}
                ownership: {agent_def.ownership.value}
                workflow:
                  type: sequential
                  checkpoints: true
                llm:
                  chat_model: "{agent_def.llm_config.chat_model}"
                  provider: "{agent_def.llm_config.provider}"
                tools: {agent_def.tools!r}
            """),
        }


def _class_name(name: str) -> str:
    return "".join(word.capitalize() for word in name.replace("-", " ").replace("_", " ").split())
