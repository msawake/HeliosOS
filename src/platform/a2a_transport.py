"""
A2A v0.2 protocol transport — HTTP client and server.

Enables Helios OS agents to:
- Be discovered via /.well-known/agent.json (Agent Card)
- Receive tasks from external agents (server)
- Call external A2A-compatible agents (client)

Protocol reference: https://a2a-protocol.org/ (v0.2)
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol data types
# ---------------------------------------------------------------------------

@dataclass
class AgentSkill:
    """A capability advertised in the Agent Card."""
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentCard:
    """A2A v0.2 Agent Card — discovery document."""
    name: str
    description: str
    url: str
    version: str = "0.2"
    provider: str = "Helios OS"
    skills: list[AgentSkill] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    authentication: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "provider": self.provider,
            "skills": [
                {"name": s.name, "description": s.description,
                 "input_schema": s.input_schema, "output_schema": s.output_schema}
                for s in self.skills
            ],
            "capabilities": self.capabilities,
            "authentication": self.authentication,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentCard:
        skills = [
            AgentSkill(
                name=s.get("name", ""),
                description=s.get("description", ""),
                input_schema=s.get("input_schema", {}),
                output_schema=s.get("output_schema", {}),
            )
            for s in data.get("skills", [])
        ]
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            url=data.get("url", ""),
            version=data.get("version", "0.2"),
            provider=data.get("provider", ""),
            skills=skills,
            capabilities=data.get("capabilities", {}),
            authentication=data.get("authentication", {}),
        )


class TaskStatus:
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class A2AMessage:
    """A message within a task."""
    role: str  # "user" or "agent"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content,
                "timestamp": self.timestamp, "metadata": self.metadata}

    @classmethod
    def from_dict(cls, data: dict) -> A2AMessage:
        return cls(role=data.get("role", "user"), content=data.get("content", ""),
                   timestamp=data.get("timestamp", ""), metadata=data.get("metadata", {}))


@dataclass
class A2AArtifact:
    """An output artifact from a task."""
    name: str
    content_type: str = "text/plain"
    data: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "content_type": self.content_type,
                "data": self.data, "metadata": self.metadata}

    @classmethod
    def from_dict(cls, data: dict) -> A2AArtifact:
        return cls(name=data.get("name", ""), content_type=data.get("content_type", "text/plain"),
                   data=data.get("data", ""), metadata=data.get("metadata", {}))


@dataclass
class A2ATask:
    """A2A v0.2 Task — the unit of work."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = TaskStatus.SUBMITTED
    messages: list[A2AMessage] = field(default_factory=list)
    artifacts: list[A2AArtifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "messages": [m.to_dict() for m in self.messages],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> A2ATask:
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            status=data.get("status", TaskStatus.SUBMITTED),
            messages=[A2AMessage.from_dict(m) for m in data.get("messages", [])],
            artifacts=[A2AArtifact.from_dict(a) for a in data.get("artifacts", [])],
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


# ---------------------------------------------------------------------------
# Agent Card generator
# ---------------------------------------------------------------------------

def generate_agent_card(agent_def: Any, base_url: str) -> AgentCard:
    """Generate an A2A Agent Card from a Helios OS agent definition."""
    name = getattr(agent_def, "name", "unknown")
    namespace = getattr(agent_def, "namespace", "default")
    description = ""
    if hasattr(agent_def, "system_prompt") and agent_def.system_prompt:
        sp = agent_def.system_prompt
        if isinstance(sp, str):
            description = sp[:200]
        elif hasattr(sp, "content"):
            description = (sp.content or "")[:200]
    if not description:
        description = f"Helios OS agent: {name}"

    tools = getattr(agent_def, "tools", []) or []
    skills = [
        AgentSkill(name=t, description=f"Tool: {t}")
        for t in tools[:10]
    ]

    return AgentCard(
        name=f"{namespace}/{name}",
        description=description,
        url=f"{base_url}/a2a/agents/{namespace}/{name}",
        skills=skills,
        capabilities={"streaming": False, "stateful": True},
    )


# ---------------------------------------------------------------------------
# Transport client
# ---------------------------------------------------------------------------

class A2ATransportClient:
    """HTTP client for calling remote A2A agents."""

    def __init__(self, timeout: int = 120):
        self._timeout = timeout

    async def get_agent_card(self, base_url: str) -> AgentCard | None:
        """Fetch agent card from /.well-known/agent.json."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{base_url}/.well-known/agent.json")
                if resp.status_code == 200:
                    return AgentCard.from_dict(resp.json())
        except ImportError:
            logger.warning("httpx not installed — cannot fetch remote agent cards")
        except Exception as e:
            logger.error("Failed to fetch agent card from %s: %s", base_url, e)
        return None

    async def send_task(self, agent_url: str, task: A2ATask) -> A2ATask:
        """Send a task to a remote A2A agent."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{agent_url}/tasks/send",
                    json={"task": task.to_dict()},
                )
                resp.raise_for_status()
                return A2ATask.from_dict(resp.json().get("task", resp.json()))
        except ImportError:
            raise RuntimeError("httpx not installed")
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.metadata["error"] = str(e)
            return task

    async def get_task(self, agent_url: str, task_id: str) -> A2ATask | None:
        """Get task status from a remote A2A agent."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{agent_url}/tasks/{task_id}")
                if resp.status_code == 200:
                    return A2ATask.from_dict(resp.json().get("task", resp.json()))
        except ImportError:
            logger.warning("httpx not installed")
        except Exception as e:
            logger.error("Failed to get task %s: %s", task_id, e)
        return None

    async def cancel_task(self, agent_url: str, task_id: str) -> bool:
        """Cancel a task on a remote A2A agent."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{agent_url}/tasks/{task_id}/cancel")
                return resp.status_code == 200
        except Exception as e:
            logger.error("Failed to cancel task %s: %s", task_id, e)
            return False


# ---------------------------------------------------------------------------
# Transport server (task handler)
# ---------------------------------------------------------------------------

class A2ATaskHandler:
    """Handles incoming A2A tasks by routing to the Helios OS executor."""

    def __init__(self, executor: Any = None, registry: Any = None):
        self._executor = executor
        self._registry = registry
        self._tasks: dict[str, A2ATask] = {}

    async def handle_send_task(self, request: dict) -> dict:
        """Handle POST /a2a/agents/{ns}/{name}/tasks/send."""
        task_data = request.get("task", request)
        task = A2ATask.from_dict(task_data)
        target_ns = request.get("namespace", "default")
        target_name = request.get("agent_name", "")

        if not self._executor:
            task.status = TaskStatus.FAILED
            task.metadata["error"] = "No executor available"
            self._tasks[task.id] = task
            return {"task": task.to_dict()}

        task.status = TaskStatus.WORKING
        task.updated_at = datetime.now(timezone.utc).isoformat()
        self._tasks[task.id] = task

        prompt = ""
        if task.messages:
            prompt = task.messages[-1].content

        try:
            agent_id = self._resolve_agent_id(target_ns, target_name)
            if not agent_id:
                task.status = TaskStatus.FAILED
                task.metadata["error"] = f"Agent {target_ns}/{target_name} not found"
                return {"task": task.to_dict()}

            result = await self._executor.invoke(agent_id, prompt)
            output = getattr(result, "output", str(result)) if not isinstance(result, dict) else result.get("output", "")
            tokens = getattr(result, "tokens_used", 0) if not isinstance(result, dict) else result.get("tokens_used", 0)

            task.status = TaskStatus.COMPLETED
            task.messages.append(A2AMessage(role="agent", content=output))
            task.artifacts.append(A2AArtifact(name="response", data=output))
            task.metadata["tokens_used"] = tokens
            task.updated_at = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.metadata["error"] = str(e)
            task.updated_at = datetime.now(timezone.utc).isoformat()

        return {"task": task.to_dict()}

    async def handle_get_task(self, task_id: str) -> dict | None:
        """Handle GET /a2a/agents/{ns}/{name}/tasks/{task_id}."""
        task = self._tasks.get(task_id)
        if not task:
            return None
        return {"task": task.to_dict()}

    async def handle_cancel_task(self, task_id: str) -> bool:
        """Handle POST /a2a/agents/{ns}/{name}/tasks/{task_id}/cancel."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELED):
            return False
        task.status = TaskStatus.CANCELED
        task.updated_at = datetime.now(timezone.utc).isoformat()
        return True

    def get_discoverable_agents(self, base_url: str) -> list[dict]:
        """Generate Agent Cards for all discoverable agents."""
        if not self._registry:
            return []
        cards = []
        for agent in self._registry.list_all():
            card = generate_agent_card(agent, base_url)
            cards.append(card.to_dict())
        return cards

    def _resolve_agent_id(self, namespace: str, name: str) -> str | None:
        if not self._registry:
            return None
        for agent in self._registry.list_all():
            agent_name = getattr(agent, "name", "")
            agent_ns = getattr(agent, "namespace", "default")
            if agent_name == name and agent_ns == namespace:
                return getattr(agent, "agent_id", name)
        return None


# ---------------------------------------------------------------------------
# Remote agent reference
# ---------------------------------------------------------------------------

@dataclass
class RemoteAgentRef:
    """Reference to an agent reachable via A2A HTTP transport."""
    name: str
    namespace: str
    url: str
    card: AgentCard | None = None
