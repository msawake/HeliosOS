"""
Platform Executor.

Central dispatcher that deploys, invokes, and manages agents across all
four stacks. Wires execution types (always-on, scheduled, event-driven,
reflex, autonomous) to the correct lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from stacks.base import (
    AgentDefinition,
    AgentResult,
    AgentStackAdapter,
    AgentStatus,
    ExecutionType,
    OwnershipType,
)
from src.platform.registry import AgentRegistry
from src.platform.scheduler import SchedulerEngine
from src.platform.event_bus import Event, EventBus

logger = logging.getLogger(__name__)

AGENTS_ROOT = Path("agents")


class PlatformExecutor:
    """
    Dispatches agent operations to the correct stack adapter and manages
    the execution lifecycle based on execution type and ownership.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        scheduler: SchedulerEngine,
        event_bus: EventBus,
        agents_root: Path | str = AGENTS_ROOT,
    ):
        self.registry = registry
        self.scheduler = scheduler
        self.event_bus = event_bus
        self.agents_root = Path(agents_root)
        self._adapters: dict[str, AgentStackAdapter] = {}
        self._autonomous_tasks: dict[str, asyncio.Task] = {}

    def register_adapter(self, adapter: AgentStackAdapter) -> None:
        self._adapters[adapter.stack_name] = adapter
        logger.info("Registered stack adapter: %s", adapter.stack_name)

    def get_adapter(self, stack: str) -> AgentStackAdapter | None:
        return self._adapters.get(stack)

    async def deploy(self, agent_def: AgentDefinition) -> str:
        """
        Full deployment pipeline:
        1. Scaffold files into agents/{personal|shared}/{name}/
        2. Register in universal registry
        3. Create agent in the stack adapter
        4. Wire execution type lifecycle
        """
        adapter = self._adapters.get(agent_def.stack)
        if not adapter:
            raise ValueError(f"No adapter registered for stack '{agent_def.stack}'")

        agent_dir = self._resolve_agent_dir(agent_def)
        agent_dir.mkdir(parents=True, exist_ok=True)

        files = adapter.scaffold_files(agent_def)
        for rel_path, content in files.items():
            file_path = agent_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)

        agent_def.config_path = str(agent_dir)
        self.registry.register(agent_def)
        await adapter.create_agent(agent_def)

        await self._wire_execution(agent_def)

        logger.info(
            "Deployed agent '%s' [stack=%s, type=%s, ownership=%s] -> %s",
            agent_def.name,
            agent_def.stack,
            agent_def.execution_type.value,
            agent_def.ownership.value,
            agent_dir,
        )
        return agent_def.agent_id

    async def invoke(self, agent_id: str, prompt: str, context: dict | None = None) -> AgentResult:
        """Invoke an agent by ID, routing to the correct stack adapter."""
        agent_def = self.registry.get(agent_id)
        if not agent_def:
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.FAILED,
                error=f"Agent {agent_id} not found in registry",
            )

        adapter = self._adapters.get(agent_def.stack)
        if not adapter:
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.FAILED,
                error=f"No adapter for stack '{agent_def.stack}'",
            )

        self.registry.set_status(agent_id, AgentStatus.RUNNING)
        try:
            result = await adapter.invoke(agent_id, prompt, context)
            self.registry.set_status(agent_id, result.status)
            return result
        except Exception as e:
            self.registry.set_status(agent_id, AgentStatus.FAILED)
            logger.exception("Agent %s invocation failed", agent_id)
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.FAILED,
                error=str(e),
            )

    async def stop_agent(self, agent_id: str) -> bool:
        agent_def = self.registry.get(agent_id)
        if not agent_def:
            return False

        adapter = self._adapters.get(agent_def.stack)
        if adapter:
            await adapter.stop(agent_id)

        if agent_id in self._autonomous_tasks:
            self._autonomous_tasks[agent_id].cancel()
            del self._autonomous_tasks[agent_id]

        self.scheduler.remove_job(agent_id)
        self.event_bus.unsubscribe(agent_id)
        self.registry.set_status(agent_id, AgentStatus.STOPPED)
        logger.info("Stopped agent %s", agent_id)
        return True

    async def undeploy(self, agent_id: str) -> bool:
        await self.stop_agent(agent_id)
        agent_def = self.registry.get(agent_id)
        if agent_def and agent_def.config_path:
            agent_dir = Path(agent_def.config_path)
            if agent_dir.exists():
                shutil.rmtree(agent_dir)
        self.registry.unregister(agent_id)
        logger.info("Undeployed agent %s", agent_id)
        return True

    def get_status(self, agent_id: str) -> dict:
        agent_def = self.registry.get(agent_id)
        if not agent_def:
            return {"agent_id": agent_id, "error": "not found"}
        return {
            "agent_id": agent_id,
            "name": agent_def.name,
            "stack": agent_def.stack,
            "execution_type": agent_def.execution_type.value,
            "ownership": agent_def.ownership.value,
            "status": self.registry.get_status(agent_id).value,
        }

    def list_agents(self, **filters) -> list[dict]:
        agents = self.registry.query(**filters) if filters else self.registry.list_all()
        return [
            {**a.to_dict(), "status": self.registry.get_status(a.agent_id).value}
            for a in agents
        ]

    async def _wire_execution(self, agent_def: AgentDefinition) -> None:
        """Set up the execution lifecycle based on execution type."""
        adapter = self._adapters[agent_def.stack]
        agent_id = agent_def.agent_id

        if agent_def.execution_type == ExecutionType.ALWAYS_ON:
            await adapter.start_loop(agent_id)
            self.registry.set_status(agent_id, AgentStatus.RUNNING)

        elif agent_def.execution_type == ExecutionType.SCHEDULED:
            if agent_def.schedule:
                async def _scheduled_callback():
                    await self.invoke(agent_id, f"Scheduled run: {agent_def.schedule}")

                self.scheduler.add_job(agent_id, agent_def.schedule, _scheduled_callback)

        elif agent_def.execution_type == ExecutionType.EVENT_DRIVEN:
            for trigger in agent_def.event_triggers:
                async def _event_callback(event: Event, _aid=agent_id):
                    await self.invoke(_aid, f"Event triggered: {event.name}", event.payload)

                self.event_bus.subscribe(trigger, agent_id, _event_callback)

        elif agent_def.execution_type == ExecutionType.REFLEX:
            pass

        elif agent_def.execution_type == ExecutionType.AUTONOMOUS:
            task = asyncio.create_task(
                self._run_autonomous_loop(agent_def),
                name=f"autonomous-{agent_id}",
            )
            self._autonomous_tasks[agent_id] = task
            self.registry.set_status(agent_id, AgentStatus.RUNNING)

    async def _run_autonomous_loop(self, agent_def: AgentDefinition) -> None:
        """Goal-directed loop: invoke repeatedly until goal is met or stopped."""
        agent_id = agent_def.agent_id
        goal = agent_def.goal or "Complete the assigned objective."
        max_iterations = agent_def.metadata.get("max_iterations", 50)
        sleep_between = agent_def.metadata.get("loop_interval_seconds", 30)

        logger.info("Starting autonomous loop for %s: goal=%s", agent_id, goal)
        for i in range(max_iterations):
            if self.registry.get_status(agent_id) == AgentStatus.STOPPED:
                break
            prompt = f"[Iteration {i + 1}/{max_iterations}] Goal: {goal}"
            result = await self.invoke(agent_id, prompt)
            if result.status == AgentStatus.COMPLETED:
                logger.info("Agent %s reached goal after %d iterations", agent_id, i + 1)
                break
            if result.status == AgentStatus.FAILED:
                logger.warning("Agent %s failed during autonomous loop", agent_id)
                break
            await asyncio.sleep(sleep_between)

        self.registry.set_status(agent_id, AgentStatus.COMPLETED)

    async def recover(self) -> int:
        """Re-wire execution for all agents loaded from persistent storage.

        Call this after boot when registry has been loaded from the database.
        Returns the number of agents that were re-wired.
        """
        agents = self.registry.list_all()
        recovered = 0
        for agent_def in agents:
            adapter = self._adapters.get(agent_def.stack)
            if not adapter:
                continue
            try:
                await adapter.create_agent(agent_def)
                await self._wire_execution(agent_def)
                recovered += 1
            except Exception:
                logger.exception("Failed to recover agent %s", agent_def.agent_id)
        if recovered:
            logger.info("Recovered %d agents from persistent store", recovered)
        return recovered

    def _resolve_agent_dir(self, agent_def: AgentDefinition) -> Path:
        if agent_def.ownership == OwnershipType.CLIENT:
            client = agent_def.owner_id or "default-client"
            return self.agents_root / "clients" / client / agent_def.name
        if agent_def.ownership == OwnershipType.PERSONAL:
            owner = agent_def.owner_id or "default"
            return self.agents_root / "personal" / owner / agent_def.name
        return self.agents_root / "shared" / agent_def.name
