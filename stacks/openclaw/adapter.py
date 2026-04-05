"""
OpenClaw Stack Adapter.

**STATUS: STUB** -- Scaffolds agents in the OpenClaw file-first pattern
(SOUL.md, IDENTITY.md, HEARTBEAT.md, SKILLS/, MEMORY/) but invocations
are currently routed through the platform LLMRouter. Real OpenClaw
gateway runtime is deferred.

TODO: Wire real OpenClaw gateway (gateway.sh launcher, SOUL.md parsing,
      HEARTBEAT.md scheduling, MEMORY/ persistence).
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
    OwnershipType,
)

logger = logging.getLogger(__name__)


class OpenClawAdapter(AgentStackAdapter):
    stack_name = "openclaw"

    def __init__(self, llm_router=None, tool_executor=None):
        self._llm_router = llm_router
        self._tool_executor = tool_executor
        self._agents: dict[str, AgentDefinition] = {}
        self._loops: dict[str, asyncio.Task] = {}

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def
        logger.info("OpenClaw agent created: %s (%s)", agent_def.name, agent_def.agent_id)
        return agent_def.agent_id

    async def invoke(self, agent_id: str, prompt: str, context: dict | None = None) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        if self._llm_router:
            from src.platform.agentic_loop import run_agentic_loop, build_tool_definitions
            tools = build_tool_definitions(self._tool_executor, agent_def.tools or None)
            system = (
                f"[SOUL] You are {agent_def.name}.\n{agent_def.description}\n\n"
                f"Use ReAct loop: Think → Act → Observe → Repeat.\n"
                f"Pause and ping on any external action. Log decisions to memory."
            )
            result = await run_agentic_loop(
                llm_router=self._llm_router,
                llm_config=agent_def.llm_config,
                system_prompt=system,
                user_prompt=prompt,
                tool_definitions=tools or None,
                tool_executor=self._tool_executor,
                agent_context={
                    "agent_id": agent_id,
                    "department": agent_def.department,
                    "client_id": agent_def.owner_id if agent_def.ownership == OwnershipType.CLIENT else None,
                    "allowed_tools": agent_def.tools or None,
                },
                context=context,
            )
            result.agent_id = agent_id
            return result

        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.COMPLETED,
            output=f"[OpenClaw simulated] Agent '{agent_def.name}' processed: {prompt[:100]}",
        )

    async def start_loop(self, agent_id: str) -> None:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return

        async def _loop():
            interval = agent_def.metadata.get("heartbeat_interval_seconds", 900)
            while True:
                try:
                    await self.invoke(agent_id, f"Heartbeat cycle for {agent_def.name}")
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("OpenClaw loop error for %s", agent_id)
                await asyncio.sleep(interval)

        self._loops[agent_id] = asyncio.create_task(_loop(), name=f"openclaw-loop-{agent_id}")

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
        schedule_section = ""
        if agent_def.schedule:
            schedule_section = f"\nSchedule: {agent_def.schedule}"

        trigger_section = ""
        if agent_def.event_triggers:
            trigger_section = "\nEvent triggers:\n" + "\n".join(
                f"- {t}" for t in agent_def.event_triggers
            )

        tools_yaml = ""
        for tool_name in agent_def.tools:
            tools_yaml += textwrap.dedent(f"""\
                name: {tool_name}
                trigger: "use {tool_name}"
                description: "Calls {tool_name} via MCP gateway"
                mcp_endpoint: "/tool/{tool_name}"
                parameters: {{}}

            """)

        return {
            "SOUL.md": textwrap.dedent(f"""\
                # SOUL

                You are {agent_def.name} — an autonomous OpenClaw agent.

                Goal: {agent_def.goal or agent_def.description or 'Assist the user effectively.'}

                Always think step-by-step. Use ReAct loop.
                Human-in-the-loop: pause and ping Slack on any external send action.

                ## Rules
                - Never guess — always confirm with MCP before external actions
                - Log every decision to MEMORY/
                - Respect rate limits and budgets
            """),
            "IDENTITY.md": textwrap.dedent(f"""\
                # IDENTITY

                Agent: {agent_def.name}
                Owner: {agent_def.owner_id or 'corporate'}
                Department: {agent_def.department or 'general'}
                Style: Professional, concise, proactive.
                Never guess — always confirm with MCP before external actions.
            """),
            "HEARTBEAT.md": textwrap.dedent(f"""\
                # HEARTBEAT
                {schedule_section or 'Every 15 minutes:'}
                - Run primary task cycle
                - Check for pending items
                - Summarize to user if needed
                {trigger_section}
            """),
            "SKILLS/default.yaml": tools_yaml or textwrap.dedent("""\
                name: default_skill
                trigger: "help"
                description: "Default skill — responds to general queries"
                mcp_endpoint: "/tool/default"
                parameters: {}
            """),
            "MEMORY/long-term.md": textwrap.dedent(f"""\
                # Long-Term Memory for {agent_def.name}

                ## Session History
                (auto-populated by the gateway runtime)

                ## Learned Preferences
                (agent updates this as it learns user patterns)
            """),
            "config.yaml": textwrap.dedent(f"""\
                name: "{agent_def.name}"
                stack: openclaw
                execution_type: {agent_def.execution_type.value}
                ownership: {agent_def.ownership.value}
                heartbeat_interval_seconds: 900
                llm:
                  chat_model: "{agent_def.llm_config.chat_model}"
                  provider: "{agent_def.llm_config.provider}"
                tools: {agent_def.tools!r}
            """),
            "gateway.sh": textwrap.dedent(f"""\
                #!/bin/bash
                # Launch OpenClaw agent: {agent_def.name}
                # Requires: node + openclaw gateway installed
                # node /opt/openclaw/gateway.js --soul SOUL.md --identity IDENTITY.md --heartbeat HEARTBEAT.md
                echo "OpenClaw agent '{agent_def.name}' — gateway placeholder"
                echo "Install openclaw runtime and update this script"
            """),
        }
