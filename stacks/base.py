"""
Base abstractions for the multi-stack agent platform.

Every agent stack (ForgeOS, CrewAI, ADK, OpenClaw) implements the
AgentStackAdapter interface so the platform can manage them uniformly.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ExecutionType(Enum):
    ALWAYS_ON = "always_on"
    SCHEDULED = "scheduled"
    EVENT_DRIVEN = "event_driven"
    REFLEX = "reflex"
    AUTONOMOUS = "autonomous"


class OwnershipType(Enum):
    PERSONAL = "personal"
    SHARED = "shared"
    CLIENT = "client"


class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    COMPLETED = "completed"
    QUARANTINED = "quarantined"


STACK_NAMES = ("forgeos", "crewai", "adk", "openclaw", "sandbox", "anthropic-agent-sdk", "anthropic-managed", "openai-agents")


@dataclass
class LLMConfig:
    chat_model: str = "claude-4-sonnet"
    reasoning_model: str | None = None
    provider: str = "anthropic"
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "chat_model": self.chat_model,
            "reasoning_model": self.reasoning_model,
            "provider": self.provider,
            "metadata": self.metadata,
        }


@dataclass
class AgentDefinition:
    name: str
    stack: str
    execution_type: ExecutionType
    ownership: OwnershipType
    agent_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    owner_id: str | None = None
    llm_config: LLMConfig = field(default_factory=LLMConfig)
    schedule: str | None = None
    event_triggers: list[str] = field(default_factory=list)
    goal: str | None = None
    tools: list[str] = field(default_factory=list)
    config_path: str = ""
    description: str = ""
    department: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""
    namespace: str = "default"  # AgentOS kernel: logical isolation group (k8s-style)

    def __post_init__(self):
        if self.stack not in STACK_NAMES:
            raise ValueError(f"stack must be one of {STACK_NAMES}, got {self.stack!r}")
        # Hydrate namespace from metadata if set there via v2 manifest
        if self.namespace == "default" and self.metadata and "_namespace" in self.metadata:
            self.namespace = self.metadata["_namespace"]

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "namespace": self.namespace,
            "stack": self.stack,
            "execution_type": self.execution_type.value,
            "ownership": self.ownership.value,
            "owner_id": self.owner_id,
            "llm_config": self.llm_config.to_dict(),
            "schedule": self.schedule,
            "event_triggers": self.event_triggers,
            "goal": self.goal,
            "tools": self.tools,
            "config_path": self.config_path,
            "description": self.description,
            "department": self.department,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
            "system_prompt": self.system_prompt,
        }


@dataclass
class AgentResult:
    agent_id: str
    status: AgentStatus
    output: str = ""
    error: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    tokens_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "tool_calls": self.tool_calls,
            "tokens_used": self.tokens_used,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model": self.model,
            "elapsed_ms": self.elapsed_ms,
        }


class AgentStackAdapter(ABC):
    """
    Interface that every agent stack must implement.

    The platform calls these methods to create, invoke, manage, and scaffold
    agents regardless of the underlying framework.
    """

    stack_name: str

    #: Whether this stack can be suspended mid-tool by the durable continuation
    #: runtime. Only stacks where the *platform* owns the LLM->tool loop
    #: (ForgeOS, Sandbox, Anthropic SDK) can park on ``ask_human`` and resume
    #: later. Frameworks that own their own loop or run in a subprocess
    #: (CrewAI, ADK, LangChain, OpenAI Agents, OpenClaw) cannot — for those the
    #: kernel downgrades a fine-grained ``ask_human`` to ``deny`` (you can't
    #: freeze a Crew.kickoff() between tool calls). Default: not suspendable.
    supports_suspend: bool = False

    @abstractmethod
    async def create_agent(self, agent_def: AgentDefinition) -> str:
        """Provision an agent from the definition. Returns agent_id."""
        ...

    @abstractmethod
    async def invoke(
        self, agent_id: str, prompt: str, context: dict | None = None,
        history: list[dict] | None = None,
    ) -> AgentResult:
        """Run a single invocation of the agent.

        When *history* is provided, it contains prior conversation turns
        (user/assistant message dicts) that should be injected between the
        system prompt and the current user message for multi-turn context.
        """
        ...

    @abstractmethod
    async def start_loop(self, agent_id: str) -> None:
        """Start a persistent loop (for always-on / autonomous agents)."""
        ...

    @abstractmethod
    async def stop(self, agent_id: str) -> None:
        """Stop a running agent."""
        ...

    @abstractmethod
    def get_status(self, agent_id: str) -> AgentStatus:
        """Return the current status of an agent."""
        ...

    @abstractmethod
    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]:
        """
        Return a dict of {relative_path: file_contents} representing the
        scaffold template for a new agent in this stack.
        """
        ...

    async def recover(self) -> int:
        """Stack-specific recovery after boot.

        Called by PlatformExecutor.recover() AFTER every agent has been
        re-registered via create_agent(). Adapters can override to rebuild
        external state (workspace files, subprocess connections, etc.).

        Default: no-op. Returns the number of items recovered.
        """
        return 0

    async def _check_kernel_gate(self, tool_name: str, tool_input: dict) -> dict | None:
        """Standardized kernel permission check for all adapters.
        
        Returns None if allowed, or an error dict if denied.
        Adapters should call this before executing any tool.
        """
        try:
            from src.forgeos_sdk.runtime import runtime as _rt
            if _rt.is_registered and _rt.is_bound:
                decision = await _rt.check_tool(tool_name, tool_input)
                if decision.denied:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Kernel denied tool %s: %s", tool_name, decision.reason
                    )
                    return {"success": False, "error": f"Kernel denied: {decision.reason}"}
                if hasattr(decision, "action") and decision.action == "rate_limit":
                    return {"success": False, "error": f"Rate limited: {decision.reason}"}
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Kernel check failed for %s: %s", tool_name, e)
            # Fail closed on kernel errors
            return {"success": False, "error": f"Kernel check failed: {e}"}
        return None


def build_agent_context(
    agent_def: AgentDefinition,
    agent_id: str,
    *,
    invocation_id: str | None = None,
) -> dict:
    """Shared helper: build the per-invocation agent_context dict.

    Every adapter should pass the result of this function as `agent_context`
    to `run_agentic_loop()`. Fields carried through:
        - agent_id, department
        - client_id (if ownership is CLIENT)
        - allowed_tools (for whitelist enforcement)
        - tenant_id, plan, monthly_limit_usd (for cost tracking in the loop)
        - invocation_id (so per-call tool handlers can derive a per-run
          workdir; populated from executor.invoke()'s run_id)
    """
    metadata = agent_def.metadata or {}
    out = {
        "agent_id": agent_id,
        "agent_name": agent_def.name,
        "namespace": agent_def.namespace,
        "department": agent_def.department,
        "client_id": agent_def.owner_id if agent_def.ownership == OwnershipType.CLIENT else None,
        "allowed_tools": agent_def.tools or None,
        "tenant_id": metadata.get("tenant_id", "default"),
        "plan": metadata.get("plan", "starter"),
        "monthly_limit_usd": metadata.get("monthly_limit_usd"),
    }
    if invocation_id:
        out["invocation_id"] = invocation_id
    return out
