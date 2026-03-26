"""
Universal Agent Registry.

Single source of truth for all agents across all stacks (ForgeOS, CrewAI,
ADK, OpenClaw). Agents are keyed by agent_id and queryable by stack,
execution type, ownership, department, and owner.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from stacks.base import (
    AgentDefinition,
    AgentStatus,
    ExecutionType,
    OwnershipType,
    STACK_NAMES,
)

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    In-memory agent registry. In production, back this with PostgreSQL
    (table: agent_registry).
    """

    def __init__(self):
        self._agents: dict[str, AgentDefinition] = {}
        self._statuses: dict[str, AgentStatus] = {}

    def register(self, agent_def: AgentDefinition) -> str:
        if agent_def.agent_id in self._agents:
            raise ValueError(f"Agent {agent_def.agent_id} already registered")
        self._agents[agent_def.agent_id] = agent_def
        self._statuses[agent_def.agent_id] = AgentStatus.IDLE
        logger.info(
            "Registered agent %s (%s) [stack=%s, type=%s, ownership=%s]",
            agent_def.name,
            agent_def.agent_id,
            agent_def.stack,
            agent_def.execution_type.value,
            agent_def.ownership.value,
        )
        return agent_def.agent_id

    def unregister(self, agent_id: str) -> bool:
        if agent_id in self._agents:
            del self._agents[agent_id]
            self._statuses.pop(agent_id, None)
            logger.info("Unregistered agent %s", agent_id)
            return True
        return False

    def get(self, agent_id: str) -> AgentDefinition | None:
        return self._agents.get(agent_id)

    def set_status(self, agent_id: str, status: AgentStatus) -> None:
        if agent_id in self._agents:
            self._statuses[agent_id] = status

    def get_status(self, agent_id: str) -> AgentStatus:
        return self._statuses.get(agent_id, AgentStatus.STOPPED)

    def list_all(self) -> list[AgentDefinition]:
        return list(self._agents.values())

    def query(
        self,
        stack: str | None = None,
        execution_type: ExecutionType | None = None,
        ownership: OwnershipType | None = None,
        owner_id: str | None = None,
        department: str | None = None,
        status: AgentStatus | None = None,
    ) -> list[AgentDefinition]:
        results = self.list_all()
        if stack:
            results = [a for a in results if a.stack == stack]
        if execution_type:
            results = [a for a in results if a.execution_type == execution_type]
        if ownership:
            results = [a for a in results if a.ownership == ownership]
        if owner_id:
            results = [a for a in results if a.owner_id == owner_id]
        if department:
            results = [a for a in results if a.department == department]
        if status:
            results = [a for a in results if self.get_status(a.agent_id) == status]
        return results

    def count_by_stack(self) -> dict[str, int]:
        counts = {s: 0 for s in STACK_NAMES}
        for a in self._agents.values():
            counts[a.stack] = counts.get(a.stack, 0) + 1
        return counts

    def count_by_execution_type(self) -> dict[str, int]:
        counts = {e.value: 0 for e in ExecutionType}
        for a in self._agents.values():
            counts[a.execution_type.value] += 1
        return counts

    def count_by_ownership(self) -> dict[str, int]:
        return {
            "personal": sum(1 for a in self._agents.values() if a.ownership == OwnershipType.PERSONAL),
            "shared": sum(1 for a in self._agents.values() if a.ownership == OwnershipType.SHARED),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "total": len(self._agents),
            "by_stack": self.count_by_stack(),
            "by_execution_type": self.count_by_execution_type(),
            "by_ownership": self.count_by_ownership(),
            "running": sum(
                1 for s in self._statuses.values() if s == AgentStatus.RUNNING
            ),
        }
