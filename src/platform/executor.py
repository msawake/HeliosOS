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
import re
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
        self._session_locks: dict[str, asyncio.Lock] = {}  # per-session_id locks

    def register_adapter(self, adapter: AgentStackAdapter) -> None:
        self._adapters[adapter.stack_name] = adapter
        logger.info("Registered stack adapter: %s", adapter.stack_name)

    def get_adapter(self, stack: str) -> AgentStackAdapter | None:
        return self._adapters.get(stack)

    async def deploy(self, agent_def: AgentDefinition) -> str:
        """
        Full deployment pipeline (crash-safe ordering):
        1. Validate agent name
        2. Register in DB first (reversible via unregister)
        3. Scaffold files into agents/{personal|shared}/{name}/
        4. Create agent in the stack adapter
        5. Wire execution type lifecycle

        If any step after registration fails, the registration is rolled back
        and scaffolded files are cleaned up.
        """
        # Validate agent name - alphanumeric, hyphens, underscores only
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]{1,63}$', agent_def.name):
            raise ValueError(
                f"Invalid agent name '{agent_def.name}'. "
                "Must start with a letter, contain only alphanumeric characters, "
                "hyphens, or underscores, and be 2-64 characters long."
            )

        # Check for path traversal
        if '..' in agent_def.name or '/' in agent_def.name or '\\' in agent_def.name:
            raise ValueError(f"Agent name contains invalid characters: '{agent_def.name}'")

        # Check uniqueness
        existing = self.registry.get(agent_def.name)
        if existing:
            raise ValueError(f"Agent '{agent_def.name}' already exists. Use a different name.")

        adapter = self._adapters.get(agent_def.stack)
        if not adapter:
            raise ValueError(f"No adapter registered for stack '{agent_def.stack}'")

        # Step 1: Register in DB first (can be rolled back)
        agent_dir = self._resolve_agent_dir(agent_def)
        agent_def.config_path = str(agent_dir)
        agent_id = self.registry.register(agent_def)

        try:
            # Step 2: Scaffold files
            agent_dir.mkdir(parents=True, exist_ok=True)
            files = adapter.scaffold_files(agent_def)
            for rel_path, content in files.items():
                file_path = agent_dir / rel_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content)

            # Step 3: Initialize in adapter
            await adapter.create_agent(agent_def)

            # Step 4: Wire execution lifecycle
            await self._wire_execution(agent_def)

        except Exception:
            # Rollback: unregister from DB
            self.registry.unregister(agent_id)
            # Cleanup files if written
            if agent_dir.exists():
                shutil.rmtree(agent_dir, ignore_errors=True)
            raise

        # Validate that referenced tools actually exist
        if agent_def.tools:
            try:
                from src.platform.agentic_loop import build_tool_definitions
                te = getattr(adapter, "_tool_executor", None)
                available = build_tool_definitions(te, None)
                available_names = {t.get("name", "") for t in available}
                missing = [t for t in agent_def.tools if t not in available_names and not t.endswith("*")]
                if missing:
                    logger.warning(
                        "Agent '%s' references tools not currently available: %s. "
                        "These tools will be unavailable at invocation time.",
                        agent_def.name, missing,
                    )
                    agent_def.metadata["_missing_tools_at_deploy"] = missing
            except Exception as e:
                logger.debug("Tool validation skipped: %s", e)

        logger.info(
            "Deployed agent '%s' [stack=%s, type=%s, ownership=%s] -> %s",
            agent_def.name,
            agent_def.stack,
            agent_def.execution_type.value,
            agent_def.ownership.value,
            agent_dir,
        )
        return agent_id

    async def invoke(
        self,
        agent_id: str,
        prompt: str,
        context: dict | None = None,
        session_id: str | None = None,
    ) -> AgentResult:
        """Invoke an agent by ID, routing to the correct stack adapter.

        When *session_id* is provided and a session store is available,
        the prior conversation history is loaded and passed to the adapter
        so the LLM sees the full multi-turn context. After invocation,
        the new user+assistant turn is appended and saved.
        """
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

        # Acquire per-session lock to prevent concurrent load/save races
        # (setdefault is atomic for dict key creation, avoiding race conditions)
        session_lock = None
        if session_id:
            session_lock = self._session_locks.setdefault(session_id, asyncio.Lock())

        async def _do_invoke():
            # Load conversation history from session store
            history: list[dict] | None = None
            session = None
            if session_id and hasattr(self, '_session_store') and self._session_store:
                session = self._session_store.get(session_id)
                if session:
                    history = session.messages

            self.registry.set_status(agent_id, AgentStatus.RUNNING)
            try:
                result = await adapter.invoke(agent_id, prompt, context, history=history)
                self.registry.set_status(agent_id, result.status)

                # Save updated conversation to session store
                if session_id and hasattr(self, '_session_store') and self._session_store:
                    if session is None:
                        from src.core.session_store import AgentSession
                        session = AgentSession(
                            session_id=session_id,
                            agent_id=agent_id,
                            tenant_id=(context or {}).get("tenant_id", "default"),
                            messages=[],
                            system_prompt=agent_def.system_prompt or agent_def.description or "",
                        )
                    session.messages.append({"role": "user", "content": prompt})
                    session.messages.append({"role": "assistant", "content": result.output or ""})
                    session.turns_completed += 1
                    session.output_tokens += result.tokens_used
                    self._session_store.save(session)

                return result
            except Exception as e:
                self.registry.set_status(agent_id, AgentStatus.FAILED)
                logger.exception("Agent %s invocation failed", agent_id)
                return AgentResult(
                    agent_id=agent_id,
                    status=AgentStatus.FAILED,
                    error=str(e),
                )

        if session_lock:
            async with session_lock:
                return await _do_invoke()
        return await _do_invoke()

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
            # REFLEX: no persistent lifecycle — agent responds to direct
            # invocations only. Mark ready for on-demand calls.
            self.registry.set_status(agent_id, AgentStatus.IDLE)

        elif agent_def.execution_type == ExecutionType.AUTONOMOUS:
            task = asyncio.create_task(
                self._run_autonomous_loop(agent_def),
                name=f"autonomous-{agent_id}",
            )
            self._autonomous_tasks[agent_id] = task
            task.add_done_callback(lambda t, aid=agent_id: self._autonomous_tasks.pop(aid, None))
            self.registry.set_status(agent_id, AgentStatus.RUNNING)

    async def _run_autonomous_loop(self, agent_def: AgentDefinition) -> None:
        """Goal-directed loop: invoke repeatedly until goal is met or stopped.

        Crash-safe: wraps each iteration in try/except. Unhandled exceptions
        increment a crash counter and apply exponential backoff. The loop
        bails out to FAILED after `max_crashes_before_give_up` consecutive
        crashes.

        Supports metadata:
          - max_iterations (default 50)
          - loop_interval_seconds (default 30)
          - restart_on_failure (default False) — if True, FAILED iterations
            don't terminate the loop immediately
          - max_crashes_before_give_up (default 3) — after this many
            consecutive crashes, force status to FAILED and exit
        """
        agent_id = agent_def.agent_id
        goal = agent_def.goal or "Complete the assigned objective."
        max_iterations = agent_def.metadata.get("max_iterations", 50)
        sleep_between = agent_def.metadata.get("loop_interval_seconds", 30)
        restart_on_failure = bool(agent_def.metadata.get("restart_on_failure", False))
        max_crashes = int(agent_def.metadata.get("max_crashes_before_give_up", 3))
        crash_count = 0
        completed = False

        logger.info("Starting autonomous loop for %s: goal=%s", agent_id, goal)

        for i in range(max_iterations):
            # Check for external stop (e.g., via executor.stop_agent)
            if self.registry.get_status(agent_id) == AgentStatus.STOPPED:
                logger.info("Autonomous loop for %s stopped externally", agent_id)
                return

            prompt = f"[Iteration {i + 1}/{max_iterations}] Goal: {goal}"
            try:
                result = await self.invoke(agent_id, prompt)
                crash_count = 0  # reset on successful invoke

                if result.status == AgentStatus.COMPLETED:
                    logger.info("Agent %s reached goal after %d iterations", agent_id, i + 1)
                    completed = True
                    break
                if result.status == AgentStatus.IDLE:
                    # Turn completed but goal not yet met — continue to next iteration
                    logger.debug("Agent %s iteration %d: goal not yet met, continuing", agent_id, i + 1)
                if result.status == AgentStatus.FAILED:
                    logger.warning("Agent %s failed during iteration %d: %s",
                                   agent_id, i + 1, result.error)
                    if not restart_on_failure:
                        self.registry.set_status(agent_id, AgentStatus.FAILED)
                        return
                    # Otherwise, fall through to sleep and retry
            except asyncio.CancelledError:
                # Task cancelled (e.g., from stop_agent) — propagate cleanly
                raise
            except Exception:
                crash_count += 1
                logger.exception(
                    "Autonomous loop crash (%d/%d) for agent %s",
                    crash_count, max_crashes, agent_id,
                )
                if crash_count >= max_crashes:
                    logger.error(
                        "Agent %s quarantined after %d consecutive crashes",
                        agent_id, crash_count,
                    )
                    self.registry.set_status(agent_id, AgentStatus.QUARANTINED)
                    return
                # Exponential backoff before retry (capped at 60s)
                backoff = min(60, 2 ** crash_count)
                await asyncio.sleep(backoff)
                continue

            await asyncio.sleep(sleep_between)

        if completed:
            self.registry.set_status(agent_id, AgentStatus.COMPLETED)
        else:
            # Iterations exhausted without a terminal state — mark IDLE so
            # the agent can be re-invoked via the regular API.
            logger.info("Autonomous loop for %s exhausted max_iterations (%d)",
                        agent_id, max_iterations)
            self.registry.set_status(agent_id, AgentStatus.IDLE)

    async def recover(self) -> int:
        """Re-wire execution for all agents loaded from persistent storage.

        Call this after boot when registry has been loaded from the database.
        Returns the number of agents that were re-wired.

        Agents in terminal states (FAILED, STOPPED) are **not** re-wired
        unless their metadata contains `restart_on_failure: true`. This
        prevents a boot-time crash loop from a bad autonomous agent.
        """
        agents = self.registry.list_all()
        recovered = 0
        skipped = 0
        stranded = []
        for agent_def in agents:
            adapter = self._adapters.get(agent_def.stack)
            if not adapter:
                stranded.append(agent_def.agent_id)
                logger.warning(
                    "Agent %s (stack=%s) stranded: no adapter registered",
                    agent_def.agent_id, agent_def.stack,
                )
                continue

            # Skip agents that were terminally failed/stopped before the crash,
            # unless explicitly opted in via metadata.
            prev_status = self.registry.get_status(agent_def.agent_id)
            # Never auto-recover quarantined agents — requires manual intervention
            if prev_status == AgentStatus.QUARANTINED:
                logger.info(
                    "Skipping recovery of %s (QUARANTINED — requires manual restart)",
                    agent_def.agent_id,
                )
                skipped += 1
                continue
            if prev_status in (AgentStatus.FAILED, AgentStatus.STOPPED):
                if not agent_def.metadata.get("restart_on_failure", False):
                    logger.info(
                        "Skipping recovery of %s (status=%s, restart_on_failure=false)",
                        agent_def.agent_id, prev_status.value,
                    )
                    skipped += 1
                    continue

            try:
                await adapter.create_agent(agent_def)
                await self._wire_execution(agent_def)
                recovered += 1
            except Exception:
                logger.exception("Failed to recover agent %s", agent_def.agent_id)
        if recovered or skipped or stranded:
            logger.info(
                "Recovered %d agents, skipped %d (terminal), stranded %d (no adapter) from persistent store",
                recovered, skipped, len(stranded),
            )

        # Per-adapter stack recovery (e.g., rewrite workspace files).
        for adapter_name, adapter in self._adapters.items():
            try:
                count = await adapter.recover()
                if count:
                    logger.info("  %s adapter recovered %d item(s)", adapter_name, count)
            except Exception:
                logger.exception("Adapter %s recover() failed", adapter_name)

        return recovered

    def _resolve_agent_dir(self, agent_def: AgentDefinition) -> Path:
        if agent_def.ownership == OwnershipType.CLIENT:
            client = agent_def.owner_id or "default-client"
            return self.agents_root / "clients" / client / agent_def.name
        if agent_def.ownership == OwnershipType.PERSONAL:
            owner = agent_def.owner_id or "default"
            return self.agents_root / "personal" / owner / agent_def.name
        return self.agents_root / "shared" / agent_def.name
