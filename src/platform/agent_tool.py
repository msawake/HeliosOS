"""
AgentTool — wraps an agent as a callable tool.

Unlike the full A2A protocol (agent__call), AgentTool is a lightweight
pattern where the LLM sees the delegated agent as just another tool.
Context isolation is enforced by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class AgentToolConfig:
    """Configuration for an agent-as-tool."""
    tool_name: str
    target_agent: str
    target_namespace: str = "default"
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task to delegate to the agent",
            },
        },
        "required": ["task"],
    })

    @classmethod
    def from_dict(cls, data: dict) -> AgentToolConfig:
        return cls(
            tool_name=data.get("name", data.get("tool_name", "")),
            target_agent=data.get("agent", data.get("target_agent", "")),
            target_namespace=data.get("namespace", data.get("target_namespace", "default")),
            description=data.get("description", ""),
            input_schema=data.get("input_schema", cls.__dataclass_fields__["input_schema"].default_factory()),
        )


class AgentTool:
    """Wraps an agent as a callable tool with context isolation."""

    def __init__(self, config: AgentToolConfig):
        self.config = config

    @property
    def name(self) -> str:
        return self.config.tool_name

    def to_tool_definition(self) -> dict:
        """Return Anthropic-format tool definition."""
        desc = self.config.description
        if not desc:
            desc = f"Delegate task to {self.config.target_namespace}/{self.config.target_agent}"
        return {
            "name": self.config.tool_name,
            "description": desc,
            "input_schema": self.config.input_schema,
        }

    async def execute(self, tool_input: dict, invoker: Any, caller_context: dict | None = None) -> dict:
        """Execute by invoking the target agent with context isolation."""
        task = tool_input.get("task", "")
        if not task:
            task = str(tool_input)

        try:
            if hasattr(invoker, "invoke"):
                result = await invoker.invoke(
                    agent_id=self._resolve_agent_id(invoker),
                    prompt=task,
                    context=None,
                    session_id=None,
                )
                return {
                    "output": getattr(result, "output", str(result)) if not isinstance(result, dict) else result.get("output", ""),
                    "status": getattr(result, "status", "completed") if not isinstance(result, dict) else result.get("status", "completed"),
                    "tokens_used": getattr(result, "tokens_used", 0) if not isinstance(result, dict) else result.get("tokens_used", 0),
                }
            return {"output": "", "status": "failed", "error": "No invoker available"}
        except Exception as e:
            logger.error("AgentTool %s execution failed: %s", self.config.tool_name, e)
            return {"output": "", "status": "failed", "error": str(e)}

    def _resolve_agent_id(self, invoker: Any) -> str:
        """Resolve agent name to agent_id via the invoker's registry."""
        if hasattr(invoker, "registry"):
            registry = invoker.registry
            if hasattr(registry, "list_all"):
                for agent in registry.list_all():
                    name = getattr(agent, "name", "")
                    ns = getattr(agent, "namespace", "default")
                    if name == self.config.target_agent and ns == self.config.target_namespace:
                        return getattr(agent, "agent_id", name)
        return self.config.target_agent


class AgentToolRegistry:
    """Registry for agent tools available to a specific agent."""

    def __init__(self):
        self._tools: dict[str, AgentTool] = {}

    def register(self, config: AgentToolConfig) -> AgentTool:
        tool = AgentTool(config)
        self._tools[tool.name] = tool
        return tool

    def register_from_manifest(self, agent_tools_config: list[dict]) -> list[AgentTool]:
        """Register tools from manifest spec.agent_tools."""
        tools = []
        for cfg in agent_tools_config:
            tool = self.register(AgentToolConfig.from_dict(cfg))
            tools.append(tool)
        return tools

    def get(self, tool_name: str) -> AgentTool | None:
        return self._tools.get(tool_name)

    def get_tool_definitions(self) -> list[dict]:
        """Return all tool definitions for LLM consumption."""
        return [t.to_tool_definition() for t in self._tools.values()]

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    def count(self) -> int:
        return len(self._tools)
