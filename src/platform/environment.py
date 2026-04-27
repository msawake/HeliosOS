"""
Environment registry — first-class compute entities that host agents.

An Environment is a K8s pod running an agent manager process. Multiple agents
can be deployed into the same Environment, sharing filesystem and resources.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class EnvironmentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class EnvironmentDefinition:
    name: str
    env_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    namespace: str = "default"
    owner_id: str | None = None
    status: EnvironmentStatus = EnvironmentStatus.PENDING
    cpu_request: str = "250m"
    cpu_limit: str = "1000m"
    mem_request: str = "512Mi"
    mem_limit: str = "1Gi"
    labels: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    agent_ids: list[str] = field(default_factory=list)
    pod_name: str = ""
    service_url: str = ""

    def to_dict(self) -> dict:
        return {
            "env_id": self.env_id,
            "name": self.name,
            "namespace": self.namespace,
            "owner_id": self.owner_id,
            "status": self.status.value,
            "cpu_request": self.cpu_request,
            "cpu_limit": self.cpu_limit,
            "mem_request": self.mem_request,
            "mem_limit": self.mem_limit,
            "labels": self.labels,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
            "agent_ids": self.agent_ids,
            "pod_name": self.pod_name,
            "service_url": self.service_url,
        }


class EnvironmentRegistry:
    """In-memory registry for environments."""

    def __init__(self):
        self._envs: dict[str, EnvironmentDefinition] = {}

    def register(self, env_def: EnvironmentDefinition) -> str:
        if env_def.env_id in self._envs:
            raise ValueError(f"Environment {env_def.env_id} already registered")
        self._envs[env_def.env_id] = env_def
        return env_def.env_id

    def get(self, env_id: str) -> EnvironmentDefinition | None:
        return self._envs.get(env_id)

    def unregister(self, env_id: str):
        self._envs.pop(env_id, None)

    def list_all(self) -> list[EnvironmentDefinition]:
        return list(self._envs.values())

    def set_status(self, env_id: str, status: EnvironmentStatus):
        env = self._envs.get(env_id)
        if env:
            env.status = status

    def add_agent(self, env_id: str, agent_id: str):
        env = self._envs.get(env_id)
        if env and agent_id not in env.agent_ids:
            env.agent_ids.append(agent_id)

    def remove_agent(self, env_id: str, agent_id: str):
        env = self._envs.get(env_id)
        if env and agent_id in env.agent_ids:
            env.agent_ids.remove(agent_id)

    def find_by_agent(self, agent_id: str) -> EnvironmentDefinition | None:
        for env in self._envs.values():
            if agent_id in env.agent_ids:
                return env
        return None

    def query(self, namespace: str | None = None, owner_id: str | None = None,
              status: EnvironmentStatus | None = None) -> list[EnvironmentDefinition]:
        results = list(self._envs.values())
        if namespace:
            results = [e for e in results if e.namespace == namespace]
        if owner_id:
            results = [e for e in results if e.owner_id == owner_id]
        if status:
            results = [e for e in results if e.status == status]
        return results
