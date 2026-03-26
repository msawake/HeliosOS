"""
Universal agent invocation wrapper.

Every agent in the company flows through `invoke_agent()`. This module:
1. Loads agent config from the registry
2. Sets up the hook chain (audit, rate-limit, auth, cost, compliance, notify)
3. Invokes the Claude Agent SDK with proper tool restrictions and MCP servers
4. Handles retries, timeouts, and error propagation
5. Returns structured results for parent orchestrators
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncGenerator

from src.core.hooks import (
    AgentContext,
    HookChain,
    HookDecision,
    create_hook_chain,
)

logger = logging.getLogger(__name__)


class AgentTier(Enum):
    HUMAN = 0
    EXECUTIVE = 1
    DEPARTMENT_LEAD = 2
    WORKER = 3


class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"


@dataclass
class AgentConfig:
    """Configuration for a single agent type."""
    agent_id: str
    name: str
    department: str
    tier: AgentTier
    system_prompt: str
    allowed_tools: list[str]
    model: str = "claude-sonnet-4-5-20250514"
    max_turns: int = 50
    timeout_seconds: int = 600
    budget_tokens: int = 500_000
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    subagents: dict[str, dict] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Structured result from an agent invocation."""
    agent_id: str
    session_id: str
    status: AgentStatus
    result: str | None = None
    error: str | None = None
    tokens_consumed: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    tool_calls: int = 0
    artifacts: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SubagentDefinition:
    """Definition for a subagent that can be spawned by an orchestrator."""
    name: str
    description: str
    system_prompt: str
    allowed_tools: list[str]
    model: str = "claude-sonnet-4-5-20250514"
    max_turns: int = 30


# ---------------------------------------------------------------------------
# Agent Registry
# ---------------------------------------------------------------------------

class AgentRegistry:
    """
    In-memory registry of all agent configurations.
    In production, this loads from PostgreSQL `agent_configs` table.
    """

    def __init__(self):
        self._configs: dict[str, AgentConfig] = {}
        self._active_sessions: dict[str, AgentStatus] = {}

    def register(self, config: AgentConfig):
        self._configs[config.agent_id] = config
        logger.info("Registered agent: %s (%s)", config.agent_id, config.name)

    def get(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def list_by_department(self, department: str) -> list[AgentConfig]:
        return [c for c in self._configs.values() if c.department == department]

    def list_by_tier(self, tier: AgentTier) -> list[AgentConfig]:
        return [c for c in self._configs.values() if c.tier == tier]

    def set_session_status(self, session_id: str, status: AgentStatus):
        self._active_sessions[session_id] = status

    def get_active_sessions(self) -> dict[str, AgentStatus]:
        return dict(self._active_sessions)

    def all_agents(self) -> list[AgentConfig]:
        return list(self._configs.values())


# ---------------------------------------------------------------------------
# Task metadata for delegation chain
# ---------------------------------------------------------------------------

@dataclass
class TaskMetadata:
    """Structured metadata attached to every delegated task."""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_task_id: str | None = None
    priority: str = "medium"  # critical | high | medium | low
    budget_tokens: int = 200_000
    deadline: str | None = None
    required_capabilities: list[str] = field(default_factory=list)
    output_format: str = "text"
    attempt_count: int = 0
    max_attempts: int = 3
    checkpoint: dict | None = None
    result_summary: str | None = None
    artifacts: list[str] = field(default_factory=list)
    needs_attention: bool = False
    circuit_broken: bool = False
    propagates_on_failure: bool = False


# ---------------------------------------------------------------------------
# Core Invoker
# ---------------------------------------------------------------------------

class AgentInvoker:
    """
    The universal agent invocation engine.
    Wraps Claude API calls with governance hooks and structured I/O.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        hook_chain: HookChain | None = None,
        config: dict | None = None,
        tool_executor=None,
        claude_client=None,
    ):
        self.registry = registry
        self.hooks = hook_chain or create_hook_chain(config=config)
        self._config = config or {}
        self._tool_executor = tool_executor
        self._claude_client = claude_client

    def _build_context(self, agent_config: AgentConfig, session_id: str) -> AgentContext:
        return AgentContext(
            agent_id=agent_config.agent_id,
            agent_type="orchestrator" if agent_config.tier.value <= 2 else "doer",
            department=agent_config.department,
            tier=agent_config.tier.value,
            session_id=session_id,
            allowed_tools=agent_config.allowed_tools,
            budget_tokens=agent_config.budget_tokens,
            model=agent_config.model,
        )

    async def invoke(
        self,
        agent_id: str,
        prompt: str,
        task_metadata: TaskMetadata | None = None,
        parent_context: AgentContext | None = None,
    ) -> AgentResult:
        """
        Invoke an agent with full governance pipeline.

        This is the main entry point. Every agent invocation in the company
        goes through this method.
        """
        agent_config = self.registry.get(agent_id)
        if not agent_config:
            return AgentResult(
                agent_id=agent_id,
                session_id="none",
                status=AgentStatus.FAILED,
                error=f"Agent {agent_id} not found in registry",
            )

        session_id = str(uuid.uuid4())
        context = self._build_context(agent_config, session_id)

        # Track session
        self.registry.set_session_status(session_id, AgentStatus.RUNNING)

        # Audit: log invocation start
        self.hooks.audit.log(
            context, "agent_start", None, None,
            decision="started",
            reasoning=f"Prompt: {prompt[:200]}..."
        )

        start_time = time.time()

        try:
            result = await self._execute_agent(
                agent_config, context, prompt, task_metadata
            )
            result.duration_seconds = time.time() - start_time

            # Audit: log completion
            self.hooks.audit.log(
                context, "agent_complete", None, None,
                decision=result.status.value,
                reasoning=result.result[:200] if result.result else None,
            )

            self.registry.set_session_status(session_id, result.status)
            return result

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            self.hooks.audit.log(
                context, "agent_timeout", None, None,
                decision="timeout",
                reasoning=f"Timed out after {duration:.1f}s",
            )
            self.hooks.slack.notify(
                "incident", f"Agent {agent_id} timed out",
                f"Session {session_id} timed out after {duration:.1f}s",
                context, "high",
            )
            self.registry.set_session_status(session_id, AgentStatus.TIMEOUT)
            return AgentResult(
                agent_id=agent_id,
                session_id=session_id,
                status=AgentStatus.TIMEOUT,
                error=f"Timed out after {duration:.1f}s",
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            self.hooks.audit.log(
                context, "agent_error", None, None,
                decision="failed",
                reasoning=str(e),
            )
            self.hooks.slack.notify(
                "incident", f"Agent {agent_id} failed",
                f"Error: {e}", context, "high",
            )
            self.registry.set_session_status(session_id, AgentStatus.FAILED)
            return AgentResult(
                agent_id=agent_id,
                session_id=session_id,
                status=AgentStatus.FAILED,
                error=str(e),
                duration_seconds=duration,
            )

    async def _execute_agent(
        self,
        config: AgentConfig,
        context: AgentContext,
        prompt: str,
        task_metadata: TaskMetadata | None,
    ) -> AgentResult:
        """
        Execute the agent via the Claude API agentic loop.

        Uses ClaudeClient for real API calls when available,
        falls back to simulation when the SDK is not installed.
        """
        full_prompt = self._build_prompt(config, prompt, task_metadata)

        # Build tool definitions from allowed_tools + custom tools
        tools = self._build_tool_definitions(config)

        agent_ctx = {
            "agent_id": config.agent_id,
            "department": config.department,
            "tier": config.tier.value,
        }

        # Use ClaudeClient if available
        if self._claude_client:
            result = await self._claude_client.run(
                system_prompt=config.system_prompt,
                prompt=full_prompt,
                model=config.model,
                tools=tools,
                max_turns=config.max_turns,
                timeout_seconds=config.timeout_seconds,
                agent_context=agent_ctx,
                hook_context=context,
            )
            status = {
                "completed": AgentStatus.COMPLETED,
                "failed": AgentStatus.FAILED,
                "timeout": AgentStatus.TIMEOUT,
            }.get(result["status"], AgentStatus.FAILED)

            return AgentResult(
                agent_id=config.agent_id,
                session_id=context.session_id,
                status=status,
                result=result.get("result"),
                error=result.get("error"),
                tokens_consumed=result.get("tokens", {"input": 0, "output": 0}),
                cost_usd=result.get("cost_usd", 0.0),
                tool_calls=result.get("tool_calls", 0),
            )

        # Simulation fallback (no ClaudeClient configured)
        return AgentResult(
            agent_id=config.agent_id,
            session_id=context.session_id,
            status=AgentStatus.COMPLETED,
            result=f"[Simulated] Agent {config.agent_id} executed task. Prompt: {prompt[:100]}",
            tokens_consumed={"input": 5000, "output": 2000},
            cost_usd=0.04,
        )

    def _build_tool_definitions(self, config: AgentConfig) -> list[dict]:
        """Build Claude API tool definitions from agent's allowed tools + custom tools."""
        tools = []

        # Add custom company tool definitions (event bus, HITL, knowledge, metrics)
        if self._tool_executor:
            for tool_def in self._tool_executor.get_custom_tool_definitions():
                if tool_def["name"] in config.allowed_tools or self._tool_matches(
                    tool_def["name"], config.allowed_tools
                ):
                    tools.append(tool_def)

        # MCP tools discovered from connected servers
        if self._tool_executor:
            for tool_def in self._tool_executor.get_mcp_tool_definitions():
                if self._tool_matches(tool_def["name"], config.allowed_tools):
                    tools.append(tool_def)

        return tools

    @staticmethod
    def _tool_matches(tool_name: str, allowed_tools: list[str]) -> bool:
        """Check if a tool name matches any pattern in allowed_tools."""
        for allowed in allowed_tools:
            if allowed == tool_name:
                return True
            if allowed.endswith("*") and tool_name.startswith(allowed[:-1]):
                return True
        return False

    def _build_prompt(
        self,
        config: AgentConfig,
        prompt: str,
        task_metadata: TaskMetadata | None,
    ) -> str:
        """Build the full prompt with system context and task metadata."""
        parts = [prompt]

        if task_metadata:
            parts.append(f"\n\n--- TASK METADATA ---")
            parts.append(f"Task ID: {task_metadata.task_id}")
            if task_metadata.parent_task_id:
                parts.append(f"Parent Task: {task_metadata.parent_task_id}")
            parts.append(f"Priority: {task_metadata.priority}")
            parts.append(f"Budget: {task_metadata.budget_tokens} tokens")
            if task_metadata.deadline:
                parts.append(f"Deadline: {task_metadata.deadline}")
            if task_metadata.required_capabilities:
                parts.append(f"Required: {', '.join(task_metadata.required_capabilities)}")
            parts.append(f"Output Format: {task_metadata.output_format}")
            parts.append(f"Attempt: {task_metadata.attempt_count + 1}/{task_metadata.max_attempts}")
            if task_metadata.checkpoint:
                parts.append(f"Checkpoint: Resume from {task_metadata.checkpoint}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Delegation helpers
# ---------------------------------------------------------------------------

async def delegate_to_subagent(
    invoker: AgentInvoker,
    parent_context: AgentContext,
    target_agent_id: str,
    prompt: str,
    task_metadata: TaskMetadata | None = None,
) -> AgentResult:
    """
    Helper for orchestrators to delegate work to a doer agent.
    Handles retry logic and result aggregation.
    """
    meta = task_metadata or TaskMetadata()

    for attempt in range(meta.max_attempts):
        meta.attempt_count = attempt

        result = await invoker.invoke(
            agent_id=target_agent_id,
            prompt=prompt,
            task_metadata=meta,
            parent_context=parent_context,
        )

        if result.status == AgentStatus.COMPLETED:
            return result

        logger.warning(
            "Agent %s attempt %d/%d failed: %s",
            target_agent_id, attempt + 1, meta.max_attempts, result.error,
        )

        if attempt < meta.max_attempts - 1:
            # Add failure context for retry
            prompt = (
                f"{prompt}\n\n--- RETRY CONTEXT ---\n"
                f"Previous attempt failed: {result.error}\n"
                f"Adjust your approach and try again."
            )

    # All retries exhausted - mark circuit broken
    meta.circuit_broken = True
    meta.needs_attention = True
    result.metadata["circuit_broken"] = True

    return result


async def delegate_parallel(
    invoker: AgentInvoker,
    parent_context: AgentContext,
    tasks: list[tuple[str, str, TaskMetadata | None]],
) -> list[AgentResult]:
    """
    Delegate multiple tasks to different agents in parallel.
    Each task is a tuple of (agent_id, prompt, optional_metadata).
    """
    coroutines = [
        delegate_to_subagent(invoker, parent_context, agent_id, prompt, meta)
        for agent_id, prompt, meta in tasks
    ]
    return await asyncio.gather(*coroutines)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_invoker(
    config: dict | None = None,
    tool_executor=None,
    claude_client=None,
) -> tuple[AgentInvoker, AgentRegistry]:
    """Create a fully configured invoker and registry."""
    registry = AgentRegistry()
    hook_chain = create_hook_chain(config=config)
    invoker = AgentInvoker(
        registry=registry,
        hook_chain=hook_chain,
        config=config,
        tool_executor=tool_executor,
        claude_client=claude_client,
    )
    return invoker, registry
