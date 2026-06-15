"""Attach/detach reusable environments to agents.

An *environment definition* (`EnvironmentDef`, migration 017) is a reusable pod
template. Attaching one to an agent (a) records the pointer on the agent
(``metadata["_env_def_id"]`` — at most one env per agent) and (b) spawns that
agent's own pod cloned from the template via the EnvironmentManager (one pod per
(env, agent)). Detaching tears the pod down and clears the pointer.

This sits above three subsystems (the def store, the agent registry, and the
EnvironmentManager) so the FastAPI layer stays thin and the flow is testable.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

ENV_DEF_META_KEY = "_env_def_id"


class EnvironmentService:
    def __init__(self, *, env_def_store: Any, registry: Any, env_mgr: Any) -> None:
        self._defs = env_def_store
        self._registry = registry
        self._env_mgr = env_mgr

    # -- attachment -----------------------------------------------------------

    def attached_def_id(self, agent_id: str) -> str | None:
        agent = self._registry.get(agent_id)
        if not agent:
            return None
        return (agent.metadata or {}).get(ENV_DEF_META_KEY)

    def _set_pointer(self, agent: Any, env_def_id: str | None) -> None:
        if agent.metadata is None:
            agent.metadata = {}
        if env_def_id is None:
            agent.metadata.pop(ENV_DEF_META_KEY, None)
        else:
            agent.metadata[ENV_DEF_META_KEY] = env_def_id
        self._registry.update(agent)

    async def attach(self, agent_id: str, env_def_id: str) -> dict[str, Any]:
        """Point the agent at the env def and spawn its pod from the template."""
        agent = self._registry.get(agent_id)
        if not agent:
            return {"ok": False, "error": f"agent {agent_id} not found"}
        d = self._defs.get(env_def_id)
        if not d:
            return {"ok": False, "error": f"environment {env_def_id} not found"}

        self._set_pointer(agent, env_def_id)
        binding = await self._env_mgr.spawn(
            agent_id, d.image,
            env_vars=d.env_vars, resources=d.resources, env_def_id=env_def_id,
        )
        return {
            "ok": binding.status == "running",
            "agent_id": agent_id,
            "env_def_id": env_def_id,
            "env_id": binding.env_id,
            "pod": binding.pod_name,
            "namespace": binding.namespace,
            "image": d.image,
            "status": binding.status,
            "error": getattr(binding, "last_error", None),
        }

    async def detach(self, agent_id: str) -> dict[str, Any]:
        """Tear down the agent's pod and clear the attachment pointer."""
        agent = self._registry.get(agent_id)
        if agent:
            self._set_pointer(agent, None)
        removed = await self._env_mgr.teardown(agent_id)
        return {"ok": True, "agent_id": agent_id, "detached": removed}

    # -- def deletion ---------------------------------------------------------

    def agents_using(self, env_def_id: str) -> list[str]:
        return [
            a.agent_id for a in self._registry.list_all()
            if (a.metadata or {}).get(ENV_DEF_META_KEY) == env_def_id
        ]

    def delete_def(self, env_def_id: str) -> dict[str, Any]:
        """Delete a template, refusing while any agent still references it."""
        in_use = self.agents_using(env_def_id)
        if in_use:
            return {"ok": False, "error": "environment is attached to agents", "agents": in_use}
        deleted = self._defs.delete(env_def_id)
        return {"ok": deleted, "env_def_id": env_def_id}
