"""
Agent-to-Agent (A2A) Protocol.

First-class primitive for agents to call other agents across any stack adapter
(forgeos, crewai, adk, openclaw, langgraph). Complements MCP (agent-to-tool)
with a symmetric agent-to-agent interface.

Design principles:
  - Addressable: calls specify (namespace, agent_name) not internal IDs
  - Permission-checked: respects spec.capabilities.a2a ACLs
  - Traced: parent_run_id + depth propagate through delegation chain
  - Loop-safe: max depth + cycle detection
  - Framework-agnostic: works the same regardless of callee's stack

Tools registered:
  - agent__call(namespace, name, task, context, timeout)       # sync
  - agent__async_call(namespace, name, task, context)          # returns job_id
  - agent__await(job_id, timeout)                              # wait for result
  - agent__list_available(namespace, department, label)        # discovery
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH = 5
DEFAULT_CALL_TIMEOUT_SECONDS = 120


@dataclass
class DelegationContext:
    """Tracks the delegation chain across nested agent calls."""

    root_run_id: str
    parent_run_id: str
    parent_agent_id: str
    depth: int = 0
    call_path: list[str] = field(default_factory=list)  # [agent_id, ...] in call order
    budget_remaining_tokens: int | None = None
    budget_remaining_usd: float | None = None

    def child(self, callee_agent_id: str) -> "DelegationContext":
        """Produce a new context for a child invocation."""
        return DelegationContext(
            root_run_id=self.root_run_id,
            parent_run_id=self.parent_run_id,
            parent_agent_id=callee_agent_id,
            depth=self.depth + 1,
            call_path=[*self.call_path, callee_agent_id],
            budget_remaining_tokens=self.budget_remaining_tokens,
            budget_remaining_usd=self.budget_remaining_usd,
        )

    def would_cycle(self, callee_agent_id: str) -> bool:
        return callee_agent_id in self.call_path


class A2APermissionError(Exception):
    """Raised when an agent lacks permission to call another."""


class A2AHandler:
    """
    Routes agent-to-agent calls. Wired into ToolExecutor as 'agent__*' tools.

    Holds a reference to the PlatformExecutor so it can invoke target agents.
    """

    def __init__(self, executor=None, max_depth: int = DEFAULT_MAX_DEPTH):
        self._executor = executor
        self._max_depth = max_depth
        # Async jobs for agent__async_call / agent__await
        self._jobs: dict[str, asyncio.Task] = {}

    def bind_executor(self, executor) -> None:
        """Attach a PlatformExecutor (set post-construction from bootstrap)."""
        self._executor = executor

    async def call(
        self,
        *,
        caller_context: dict,
        target_namespace: str,
        target_name: str,
        task: str,
        context: dict | None = None,
        timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS,
    ) -> dict:
        """Synchronous agent-to-agent call. Returns the callee's AgentResult as dict."""
        if not self._executor:
            return {"success": False, "error": "A2A not initialized (no platform executor)"}

        # 1. Resolve callee
        callee_def = self._resolve_agent(target_namespace, target_name)
        if not callee_def:
            return {"success": False, "error": f"Agent {target_namespace}/{target_name} not found"}

        # 2. Check delegation chain depth
        delegation = caller_context.get("_delegation") if caller_context else None
        if delegation and delegation.depth >= self._max_depth:
            return {"success": False, "error": f"Delegation depth exceeded ({self._max_depth})"}

        # 3. Check for cycles
        if delegation and delegation.would_cycle(callee_def.agent_id):
            return {
                "success": False,
                "error": f"Delegation cycle detected — {callee_def.agent_id} already in call path",
            }

        # 4. Check A2A permission
        caller_namespace = (caller_context or {}).get("namespace", "default")
        caller_agent_name = (caller_context or {}).get("agent_name", "")
        if not self._check_permission(callee_def, caller_namespace, caller_agent_name):
            return {
                "success": False,
                "error": (
                    f"A2A permission denied: {caller_namespace}/{caller_agent_name} "
                    f"may not call {target_namespace}/{target_name}"
                ),
            }

        # 5. Build child delegation context
        child_delegation = (
            delegation.child(callee_def.agent_id) if delegation
            else DelegationContext(
                root_run_id=str(uuid.uuid4())[:12],
                parent_run_id=str(uuid.uuid4())[:12],
                parent_agent_id=callee_def.agent_id,
                depth=1,
                call_path=[callee_def.agent_id],
            )
        )

        merged_context = dict(context or {})
        merged_context["_delegation"] = child_delegation
        merged_context["_caller"] = {
            "namespace": caller_namespace,
            "agent_name": caller_agent_name,
        }

        # 6. Invoke the callee
        try:
            result = await asyncio.wait_for(
                self._executor.invoke(callee_def.agent_id, task, merged_context),
                timeout=timeout,
            )
            logger.info(
                "A2A call: %s/%s -> %s/%s (depth=%d, status=%s)",
                caller_namespace, caller_agent_name,
                target_namespace, target_name,
                child_delegation.depth,
                result.status.value if hasattr(result.status, "value") else result.status,
            )
            return {
                "success": True,
                "agent_id": callee_def.agent_id,
                "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                "output": result.output,
                "tokens_used": result.tokens_used,
                "tool_calls": [
                    {"name": tc.get("name"), "input": tc.get("input")}
                    for tc in (result.tool_calls or [])
                ],
                "error": result.error,
                "delegation_path": child_delegation.call_path,
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": f"A2A call timed out after {timeout}s"}
        except Exception as e:
            logger.exception("A2A call failed")
            return {"success": False, "error": f"A2A call failed: {e}"}

    async def async_call(
        self,
        *,
        caller_context: dict,
        target_namespace: str,
        target_name: str,
        task: str,
        context: dict | None = None,
    ) -> dict:
        """Fire-and-forget variant. Returns a job_id immediately."""
        job_id = str(uuid.uuid4())[:12]
        task_coro = self.call(
            caller_context=caller_context,
            target_namespace=target_namespace,
            target_name=target_name,
            task=task,
            context=context,
        )
        self._jobs[job_id] = asyncio.create_task(task_coro, name=f"a2a-{job_id}")
        return {"success": True, "job_id": job_id}

    async def await_job(self, job_id: str, timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS) -> dict:
        """Wait for an async call to finish."""
        task = self._jobs.get(job_id)
        if not task:
            return {"success": False, "error": f"Unknown job_id: {job_id}"}
        try:
            result = await asyncio.wait_for(task, timeout=timeout)
            self._jobs.pop(job_id, None)
            return result
        except asyncio.TimeoutError:
            return {"success": False, "error": f"Job {job_id} not yet complete"}

    def list_available(
        self,
        *,
        namespace: str | None = None,
        department: str | None = None,
    ) -> list[dict]:
        """Return discoverable agents the caller could potentially call."""
        if not self._executor:
            return []
        agents = self._executor.registry.list_all()
        result = []
        for a in agents:
            if namespace and a.namespace != namespace:
                continue
            if department and a.department != department:
                continue
            result.append({
                "name": a.name,
                "namespace": a.namespace,
                "agent_id": a.agent_id,
                "description": a.description,
                "department": a.department,
                "stack": a.stack,
            })
        return result

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    def _resolve_agent(self, namespace: str, name: str):
        """Find an agent by (namespace, name). Returns AgentDefinition or None."""
        if not self._executor:
            return None
        for a in self._executor.registry.list_all():
            if a.name == name and a.namespace == namespace:
                return a
        return None

    def _check_permission(self, callee_def, caller_namespace: str, caller_name: str) -> bool:
        """Check if caller is allowed to invoke callee based on callee's A2A ACL.

        ACL is stored in callee's metadata under '_capabilities.a2a.canBeCalledBy'.
        If no ACL is set, default permits: same-namespace calls.
        """
        capabilities = (callee_def.metadata or {}).get("_capabilities", {})
        a2a_cfg = capabilities.get("a2a", {})
        acl = a2a_cfg.get("canBeCalledBy") or []

        # No ACL declared -> default permit same-namespace
        if not acl:
            return caller_namespace == callee_def.namespace

        # Check each allowed peer spec
        for peer in acl:
            peer_ns = peer.get("namespace", "default")
            peer_agents = peer.get("agents") or []
            peer_roles = peer.get("roles") or []

            # Namespace match
            if peer_ns == caller_namespace or peer_ns == "*":
                # If specific agents listed, caller must be one of them
                if peer_agents and caller_name not in peer_agents:
                    continue
                # Roles are TODO (would require caller's role set)
                return True
        return False


# ---------------------------------------------------------------------------
# Tool schemas (for the LLM)
# ---------------------------------------------------------------------------

A2A_TOOL_SCHEMAS = [
    {
        "name": "agent__call",
        "description": (
            "Call another agent synchronously and wait for its response. Use for "
            "delegating specialized work. Respects permission ACLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Target agent namespace", "default": "default"},
                "name": {"type": "string", "description": "Target agent name"},
                "task": {"type": "string", "description": "Task/prompt for the callee"},
                "context": {"type": "object", "description": "Additional context to pass"},
                "timeout": {"type": "number", "description": "Seconds to wait", "default": 120},
            },
            "required": ["name", "task"],
        },
    },
    {
        "name": "agent__async_call",
        "description": "Fire an async call to another agent. Returns a job_id immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "default": "default"},
                "name": {"type": "string"},
                "task": {"type": "string"},
                "context": {"type": "object"},
            },
            "required": ["name", "task"],
        },
    },
    {
        "name": "agent__await",
        "description": "Wait for an async agent call to complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "timeout": {"type": "number", "default": 120},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "agent__list_available",
        "description": "List agents that can be called. Filter by namespace or department.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "department": {"type": "string"},
            },
        },
    },
]
