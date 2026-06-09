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
from src.platform.checkpoint import (
    Checkpoint,
    CheckpointStore,
    LoopProgress,
    MemoryCheckpointStore,
)
from src.platform.process import AgentIdentity, Phase, ProcessTable
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
        process_table: ProcessTable | None = None,
        checkpoint_store: CheckpointStore | None = None,
    ):
        self.registry = registry
        self.scheduler = scheduler
        self.event_bus = event_bus
        self.agents_root = Path(agents_root)
        self.process_table = process_table or ProcessTable(registry=registry)
        # Late-bind registry so the table can mirror phase -> legacy status.
        if self.process_table._registry is None:
            self.process_table.attach_registry(registry)
        self.checkpoint_store: CheckpointStore = checkpoint_store or MemoryCheckpointStore()
        self._adapters: dict[str, AgentStackAdapter] = {}
        self._autonomous_tasks: dict[str, asyncio.Task] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}  # per-session_id locks
        self.callback_registry = None  # wired by bootstrap if callbacks module available
        # PIDs whose invoke() is currently in flight. Drives the "scheduled vs
        # actively running" distinction surfaced by the Fleet endpoint — a
        # scheduled agent reports as SCHEDULED between cron ticks and only
        # flips to RUNNING while one of its invocations is executing.
        self._invoking_pids: set[str] = set()
        # Optional per-invocation history store; bootstrap sets it when a DB
        # pool exists. When None, all run-history writes are no-ops.
        from src.platform.agent_runs_store import AgentRunsStore
        self.agent_runs: AgentRunsStore = AgentRunsStore(None)
        # Per-user credential store (e.g. GitHub PATs). Bootstrap attaches one
        # if Secret Manager is reachable; remains None in pure dev/test runs.
        self.credential_store = None

    def attach_credential_store(self, store) -> None:
        """Bind a CredentialStore so invoke() can inject per-user secrets."""
        self.credential_store = store

    def _register_process(self, agent_def: AgentDefinition) -> None:
        """Create a process-table entry for a freshly-registered agent."""
        identity = AgentIdentity(
            pid=agent_def.agent_id,
            name=agent_def.name,
            namespace=agent_def.namespace,
            owner_id=agent_def.owner_id,
            tenant_id=(agent_def.metadata or {}).get("tenant_id", "default"),
            parent_pid=(agent_def.metadata or {}).get("parent_pid"),
        )
        try:
            self.process_table.register(identity, spec_ref=agent_def.agent_id, phase=Phase.ADMITTED)
        except ValueError:
            # Already registered (e.g. recover() after boot). Ensure phase at least reflects ADMITTED.
            self.process_table.transition(agent_def.agent_id, Phase.ADMITTED, force=True)

    def _save_checkpoint(
        self,
        agent_id: str,
        *,
        step_index: int,
        max_iterations: int | None = None,
        crash_count: int = 0,
        goal: str | None = None,
        last_output_summary: str | None = None,
    ) -> None:
        """Snapshot the process at a stable loop boundary."""
        proc = self.process_table.get(agent_id)
        if proc is None:
            return
        progress = LoopProgress(
            step_index=step_index,
            max_iterations=max_iterations,
            crash_count=crash_count,
            goal=goal,
            last_output_summary=last_output_summary,
        )
        checkpoint = Checkpoint.from_process(proc, loop_progress=progress)
        try:
            self.checkpoint_store.save(checkpoint)
        except Exception:
            # Checkpoint failures must not kill the agent — they only degrade recovery.
            logger.exception("Failed to save checkpoint for %s", agent_id)

    def _resume_point(self, agent_id: str) -> dict[str, int]:
        """Load the saved resume point (step/crash) for an autonomous loop.

        Returns zeroed defaults if no checkpoint exists. Silently discards
        any checkpoint whose generation no longer matches the live process,
        since that indicates a spec change that invalidates the snapshot.
        """
        try:
            checkpoint = self.checkpoint_store.load(agent_id)
        except Exception:
            logger.exception("Failed to load checkpoint for %s", agent_id)
            return {"step_index": 0, "crash_count": 0}
        if not checkpoint:
            return {"step_index": 0, "crash_count": 0}
        proc = self.process_table.get(agent_id)
        if proc is not None and checkpoint.generation != proc.identity.generation:
            logger.info(
                "Discarding stale checkpoint for %s (gen=%d vs proc=%d)",
                agent_id, checkpoint.generation, proc.identity.generation,
            )
            self.checkpoint_store.delete(agent_id)
            return {"step_index": 0, "crash_count": 0}
        return {
            "step_index": checkpoint.loop_progress.step_index,
            "crash_count": checkpoint.loop_progress.crash_count,
        }

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
        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]{1,63}$', agent_def.name):
            raise ValueError(
                f"Invalid agent name '{agent_def.name}'. "
                "Must start with a letter, contain only alphanumeric characters, "
                "hyphens, or underscores, and be 2-64 characters long."
            )

        # Check uniqueness by name (registry.get uses agent_id, not name)
        for existing in self.registry.list_all():
            if existing.name == agent_def.name and existing.namespace == agent_def.namespace:
                raise ValueError(
                    f"Agent '{agent_def.namespace}/{agent_def.name}' already exists "
                    f"(id={existing.agent_id}). Use a different name."
                )

        # Tier-based routing: untrusted agents (tier >= 3) run in sandbox
        tier = (agent_def.metadata or {}).get("_tier", 1)
        if tier >= 3 and "sandbox" in self._adapters and agent_def.stack != "sandbox":
            logger.info("Tier %d agent '%s' routed to sandbox (was %s)", tier, agent_def.name, agent_def.stack)
            agent_def.stack = "sandbox"

        adapter = self._adapters.get(agent_def.stack)
        if not adapter:
            raise ValueError(f"No adapter registered for stack '{agent_def.stack}'")

        # Step 1: Register in DB first (can be rolled back)
        agent_dir = self._resolve_agent_dir(agent_def)
        agent_def.config_path = str(agent_dir)
        agent_id = self.registry.register(agent_def)
        self._register_process(agent_def)

        try:
            # Step 2: Scaffold files
            agent_dir.mkdir(parents=True, exist_ok=True)
            files = adapter.scaffold_files(agent_def)
            for rel_path, content in files.items():
                file_path = agent_dir / rel_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(content)

            # Step 3: Initialize in adapter
            self.process_table.transition(agent_id, Phase.STARTING)
            await adapter.create_agent(agent_def)

            # Step 4: Wire execution lifecycle
            await self._wire_execution(agent_def)
            self.process_table.transition(agent_id, Phase.RUNNING)

        except Exception as exc:
            # Rollback: unregister from DB
            self.process_table.transition(
                agent_id, Phase.FAILED, reason=f"deploy failed: {exc}", force=True
            )
            self.process_table.unregister(agent_id)
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

        trigger = (context or {}).get("_trigger", "manual")
        tenant_id = (context or {}).get("tenant_id", "default")

        async def _do_invoke():
            # Load conversation history from session store
            history: list[dict] | None = None
            session = None
            if session_id and hasattr(self, '_session_store') and self._session_store:
                session = self._session_store.get(session_id)
                if session:
                    history = session.messages

            self.registry.set_status(agent_id, AgentStatus.RUNNING)
            self.process_table.heartbeat(agent_id)
            self._invoking_pids.add(agent_id)
            run_id = await self.agent_runs.start(
                pid=agent_id, agent_id=agent_id, trigger=trigger,
                prompt=prompt, tenant_id=tenant_id,
            )
            import time as _time
            _t0 = _time.monotonic()

            # Bind the SDK runtime so agent code can use
            # ``from forgeos_sdk import runtime`` without passing agent_id.
            _rt_token = None
            try:
                from src.forgeos_sdk.runtime import runtime as _sdk_runtime
                if _sdk_runtime.is_registered:
                    _rt_token = _sdk_runtime.bind(
                        agent_id,
                        namespace=getattr(agent_def, "namespace", "default"),
                    )
            except Exception:
                pass

            try:
                invoke_ctx = dict(context or {})
                # Surface the run id so per-call tool handlers (e.g. the
                # forgeos-lens dev tools) can derive a per-invocation workdir
                # — two concurrent runs must not share /tmp scratch space.
                if run_id and "invocation_id" not in invoke_ctx:
                    invoke_ctx["invocation_id"] = run_id
                if self.callback_registry:
                    invoke_ctx["_callback_registry"] = self.callback_registry
                # Resolve the acting user and surface it on the invoke context so
                # build_agent_context routes per-user MCP / credentials for
                # scheduled + A2A runs too (those carry no HTTP X-Forgeos-User
                # header — only the HTTP /invoke path sets user_id itself).
                user_id = (
                    invoke_ctx.get("user_id")
                    or getattr(agent_def, "owner_id", None)
                    or "default"
                )
                invoke_ctx["user_id"] = user_id
                # Per-user credentials (GH PAT, etc.) — injected into the
                # invocation context only, never on os.environ, so concurrent
                # invocations for different users don't race.
                if self.credential_store is not None:
                    try:
                        self.credential_store.inject_for_invocation(
                            invoke_ctx,
                            user_id=user_id,
                            caller=f"executor.invoke:{agent_id}",
                            invocation_id=run_id or "",
                        )
                    except Exception:
                        logger.debug("credential injection failed", exc_info=True)
                result = await adapter.invoke(agent_id, prompt, invoke_ctx, history=history)
                # Self-heal: if the adapter forgot the agent (e.g. a process
                # restart before recovery wired adapters, or a stale stop),
                # re-create from the registry definition and retry once. This
                # makes RUN NOW work for previously-stopped agents without
                # forcing the operator to re-upload the manifest.
                if (
                    result.status == AgentStatus.FAILED
                    and result.error
                    and "Agent not found" in result.error
                ):
                    try:
                        await adapter.create_agent(agent_def)
                        result = await adapter.invoke(agent_id, prompt, invoke_ctx, history=history)
                    except Exception:
                        logger.exception("Self-heal create_agent failed for %s", agent_id)
                self.registry.set_status(agent_id, result.status)

                # Record runtime accounting on the process. Invoke-level
                # IDLE/COMPLETED/FAILED does not change the process phase —
                # only start/stop/wire_execution do.
                _in = getattr(result, "input_tokens", 0) or 0
                _out = getattr(result, "output_tokens", 0) or 0
                # If the provider didn't return a split, attribute the whole
                # bill to tokens_out so the total still matches.
                if not _in and not _out:
                    _out = result.tokens_used or 0
                _model = getattr(result, "model", None)
                _dollars = 0.0
                if _model and (_in or _out):
                    try:
                        from src.billing.plans import estimate_cost_usd
                        _dollars = estimate_cost_usd(_model, _in, _out)
                    except Exception:
                        _dollars = 0.0
                self.process_table.record_usage(
                    agent_id,
                    tokens_in=_in,
                    tokens_out=_out,
                    dollars=_dollars,
                    tool_calls=len(result.tool_calls or []),
                    wallclock_ms=result.elapsed_ms or 0.0,
                )
                self.process_table.heartbeat(agent_id)

                # Save updated conversation to session store
                if session_id and hasattr(self, '_session_store') and self._session_store:
                    new_msgs = [
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": result.output or ""},
                    ]
                    if session is None:
                        from src.core.session_store import AgentSession
                        session = AgentSession(
                            session_id=session_id,
                            agent_id=agent_id,
                            tenant_id=(context or {}).get("tenant_id", "default"),
                            messages=new_msgs,
                            system_prompt=agent_def.system_prompt or agent_def.description or "",
                        )
                        session.turns_completed = 1
                        session.output_tokens = result.tokens_used
                        self._session_store.save(session)
                    else:
                        session.messages.extend(new_msgs)
                        session.turns_completed += 1
                        session.output_tokens += result.tokens_used
                        if hasattr(self._session_store, "append_messages"):
                            self._session_store.append_messages(session_id, new_msgs)
                        else:
                            self._session_store.save(session)

                return result
            except Exception as e:
                self.registry.set_status(agent_id, AgentStatus.FAILED)
                logger.exception("Agent %s invocation failed", agent_id)
                result = AgentResult(
                    agent_id=agent_id,
                    status=AgentStatus.FAILED,
                    error=str(e),
                )
                return result
            finally:
                if _rt_token is not None:
                    try:
                        _sdk_runtime.unbind(_rt_token)
                    except Exception:
                        pass
                self._invoking_pids.discard(agent_id)
                # If the agent left A2H requests pending, park it in
                # AWAITING_HUMAN. The human's approve/reject endpoint will
                # transition it back to RUNNING and (when needed) kick a
                # resume invoke so any deferred tool calls finish.
                try:
                    gw = getattr(self, "_a2h_gateway", None)
                    if gw is not None and hasattr(gw, "list_pending_from"):
                        if gw.list_pending_from(agent_id):
                            self.process_table.transition(
                                agent_id, Phase.AWAITING_HUMAN, force=True,
                                reason="pending A2H request(s)",
                            )
                except Exception:
                    logger.debug("AWAITING_HUMAN transition failed", exc_info=True)
                # Worker-tier invokes return immediately after ENQUEUING (the
                # worker pool does the real run async). The agent itself is not
                # executing inline, so don't leave it stuck as RUNNING — the
                # live run state lives in the continuation/runs, not the agent's
                # top-level status.
                try:
                    _r = locals().get("result")
                    if _r is not None and (getattr(_r, "metadata", None) or {}).get("queued"):
                        self.registry.set_status(agent_id, AgentStatus.IDLE)
                except Exception:
                    logger.debug("queued-run status reset failed", exc_info=True)
                try:
                    _final = locals().get("result")
                    if _final is not None:
                        _status = "completed" if _final.status == AgentStatus.COMPLETED else (
                            "failed" if _final.status == AgentStatus.FAILED else str(_final.status.value)
                        )
                        await self.agent_runs.finish(
                            run_id,
                            status=_status,
                            output=(_final.output or "")[:8000] if _final.output else None,
                            error=_final.error,
                            tool_calls=len(_final.tool_calls or []),
                            tokens_used=_final.tokens_used or 0,
                            input_tokens=getattr(_final, "input_tokens", 0) or 0,
                            output_tokens=getattr(_final, "output_tokens", 0) or 0,
                            model=getattr(_final, "model", None),
                            duration_ms=int((_time.monotonic() - _t0) * 1000),
                        )
                except Exception:
                    logger.debug("agent_runs.finish bookkeeping failed", exc_info=True)

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
        # DRAINING lets sideband logic (FAILED, evicted) win over a normal stop.
        self.process_table.transition(agent_id, Phase.DRAINING)
        self.registry.set_status(agent_id, AgentStatus.STOPPED)
        self.process_table.transition(agent_id, Phase.STOPPED)
        # Stop is a clean termination — drop any saved checkpoint.
        try:
            self.checkpoint_store.delete(agent_id)
        except Exception:
            logger.debug("checkpoint delete on stop failed for %s", agent_id)
        logger.info("Stopped agent %s", agent_id)
        return True

    async def undeploy(self, agent_id: str) -> bool:
        await self.stop_agent(agent_id)
        agent_def = self.registry.get(agent_id)
        if agent_def and agent_def.config_path:
            agent_dir = Path(agent_def.config_path)
            if agent_dir.exists():
                shutil.rmtree(agent_dir)
        self.process_table.unregister(agent_id)
        try:
            self.checkpoint_store.delete(agent_id)
        except Exception:
            logger.debug("checkpoint delete on undeploy failed for %s", agent_id)
        self.registry.unregister(agent_id)
        logger.info("Undeployed agent %s", agent_id)
        return True

    # ------------------------------------------------------------------
    # Team deployment
    # ------------------------------------------------------------------

    async def deploy_team(self, team_manifest) -> list[str]:
        """Deploy all agents in a team with pre-wired relationships."""
        from src.forgeos_sdk.manifest import TeamManifest
        if isinstance(team_manifest, dict):
            team_manifest = TeamManifest.from_dict(team_manifest)

        team_name = team_manifest.metadata.name
        namespace = team_manifest.metadata.namespace
        orchestration = team_manifest.spec.orchestration
        agent_ids: list[str] = []
        supervisor_id: str | None = None
        agent_names = [a.name for a in team_manifest.spec.agents]

        for i, agent_spec in enumerate(team_manifest.spec.agents):
            agent_def = self._build_agent_from_team(team_manifest, agent_spec)

            # Wire A2A ACLs based on orchestration
            self._wire_team_a2a(team_manifest, agent_spec, agent_def, agent_names, i)

            # Deploy the agent
            agent_id = await self.deploy(agent_def)
            agent_ids.append(agent_id)

            # Track supervisor for parent_pid wiring
            if orchestration == "supervisor" and agent_spec.role == "supervisor":
                supervisor_id = agent_id

            # Set parent_pid for workers in supervisor pattern
            if orchestration == "supervisor" and agent_spec.role != "supervisor" and supervisor_id:
                proc = self.process_table.get(agent_id)
                if proc and hasattr(proc, 'identity') and hasattr(proc.identity, 'parent_pid'):
                    proc.identity.parent_pid = supervisor_id

        logger.info(
            "TEAM DEPLOYED | %s/%s | %d agents | orchestration=%s",
            namespace, team_name, len(agent_ids), orchestration,
        )
        return agent_ids

    async def undeploy_team(self, team_name: str, namespace: str = "default") -> int:
        """Undeploy all agents belonging to a team."""
        count = 0
        for agent in list(self.registry.list_all()):
            agent_meta = getattr(agent, 'metadata', {}) or {}
            if isinstance(agent_meta, dict):
                if agent_meta.get("_team") == team_name and getattr(agent, 'namespace', 'default') == namespace:
                    try:
                        await self.stop_agent(getattr(agent, 'agent_id', agent.name))
                        count += 1
                    except Exception as e:
                        logger.error("Failed to undeploy team agent %s: %s", agent.name, e)
        logger.info("TEAM UNDEPLOYED | %s/%s | %d agents removed", namespace, team_name, count)
        return count

    def _build_agent_from_team(self, team_manifest, agent_spec) -> AgentDefinition:
        """Build an AgentDefinition by merging team defaults with agent overrides."""
        from stacks.base import LLMConfig as StackLLMConfig

        defaults = team_manifest.spec.defaults
        namespace = team_manifest.metadata.namespace
        team_name = team_manifest.metadata.name
        shared = team_manifest.spec.shared_context

        # Merge LLM config: agent overrides team defaults
        llm_config: StackLLMConfig | None = None
        source_llm = agent_spec.llm or defaults.llm
        if source_llm:
            llm_config = StackLLMConfig(
                chat_model=source_llm.chat_model,
                provider=source_llm.provider,
                metadata=source_llm.metadata if hasattr(source_llm, 'metadata') else {},
            )

        # Merge tools: agent tools + shared tools
        tools = list(agent_spec.tools)
        if shared and shared.shared_tools:
            for t in shared.shared_tools:
                if t not in tools:
                    tools.append(t)

        # Determine stack and execution type
        stack = agent_spec.stack or defaults.stack or "forgeos"
        exec_type = agent_spec.execution_type or "reflex"

        # Build system prompt
        system_prompt = ""
        if agent_spec.system_prompt:
            if isinstance(agent_spec.system_prompt, str):
                system_prompt = agent_spec.system_prompt
            elif hasattr(agent_spec.system_prompt, 'content'):
                system_prompt = agent_spec.system_prompt.content or ""

        # Build metadata with team reference
        metadata = dict(agent_spec.metadata)
        metadata["_team"] = team_name
        metadata["_team_role"] = agent_spec.role
        metadata["_namespace"] = namespace

        # Merge boundaries
        if agent_spec.boundaries:
            metadata["_boundaries"] = (
                agent_spec.boundaries.model_dump()
                if hasattr(agent_spec.boundaries, 'model_dump')
                else {}
            )
        elif defaults.boundaries:
            metadata["_boundaries"] = (
                defaults.boundaries.model_dump()
                if hasattr(defaults.boundaries, 'model_dump')
                else {}
            )

        return AgentDefinition(
            name=agent_spec.name,
            stack=stack,
            execution_type=ExecutionType(exec_type),
            ownership=OwnershipType.SHARED,
            tools=tools,
            llm_config=llm_config or StackLLMConfig(),
            system_prompt=system_prompt,
            schedule=agent_spec.schedule,
            event_triggers=agent_spec.event_triggers,
            goal=agent_spec.goal,
            metadata=metadata,
        )

    def _wire_team_a2a(
        self,
        team_manifest,
        agent_spec,
        agent_def: AgentDefinition,
        all_agent_names: list[str],
        index: int,
    ) -> None:
        """Set canCall/canBeCalledBy based on orchestration pattern."""
        namespace = team_manifest.metadata.namespace
        orchestration = team_manifest.spec.orchestration
        other_names = [n for n in all_agent_names if n != agent_spec.name]

        capabilities = agent_def.metadata.get("_capabilities", {})
        a2a = capabilities.get("a2a", {})

        if orchestration == "supervisor":
            if agent_spec.role == "supervisor":
                a2a["canCall"] = [{"namespace": namespace, "agents": other_names}]
            else:
                supervisors = [a.name for a in team_manifest.spec.agents if a.role == "supervisor"]
                a2a["canBeCalledBy"] = [{"namespace": namespace, "agents": supervisors}]

        elif orchestration == "sequential":
            if index < len(all_agent_names) - 1:
                a2a["canCall"] = [{"namespace": namespace, "agents": [all_agent_names[index + 1]]}]
            if index > 0:
                a2a["canBeCalledBy"] = [{"namespace": namespace, "agents": [all_agent_names[index - 1]]}]

        elif orchestration == "mesh":
            a2a["canCall"] = [{"namespace": namespace, "agents": other_names}]
            a2a["canBeCalledBy"] = [{"namespace": namespace, "agents": other_names}]

        elif orchestration == "parallel":
            pass  # No cross-calling

        capabilities["a2a"] = a2a
        agent_def.metadata["_capabilities"] = capabilities

    def get_status(self, agent_id: str) -> dict:
        agent_def = self.registry.get(agent_id)
        if not agent_def:
            return {"agent_id": agent_id, "error": "not found"}
        proc = self.process_table.get(agent_id)
        out = {
            "agent_id": agent_id,
            "name": agent_def.name,
            "stack": agent_def.stack,
            "execution_type": agent_def.execution_type.value,
            "ownership": agent_def.ownership.value,
            "status": self.registry.get_status(agent_id).value,
        }
        if proc is not None:
            out["phase"] = proc.phase.value
            out["resource_usage"] = proc.resource_usage.to_dict()
            out["last_error"] = proc.last_error
        return out

    def list_agents(self, **filters) -> list[dict]:
        agents = self.registry.query(**filters) if filters else self.registry.list_all()
        out = []
        for a in agents:
            entry: dict = {**a.to_dict(), "status": self.registry.get_status(a.agent_id).value}
            proc = self.process_table.get(a.agent_id)
            if proc is not None:
                entry["phase"] = proc.phase.value
                entry["resource_usage"] = proc.resource_usage.to_dict()
            out.append(entry)
        return out

    def ps(self) -> list[dict]:
        """Return a ``ps(1)``-style view of the process table.

        One row per agent process with stable PID, phase, tenant, parent,
        cumulative tokens/dollars/tool-calls/wallclock, heartbeat, and last
        error. Intended for CLI (``forgeos ps``) and dashboard consumption.
        """
        return self.process_table.ps()

    def process_summary(self) -> dict:
        """Return a histogram of process phases (``{phase: count, total: N}``)."""
        return self.process_table.summary()

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

        # Resume from checkpoint if one exists for this PID — this is how
        # autonomous agents survive a platform restart. Step index is the
        # iteration we will *begin* on after restore.
        resume_point = self._resume_point(agent_id)
        start_iter = resume_point["step_index"]
        crash_count = resume_point["crash_count"]
        completed = False

        if start_iter > 0:
            logger.info(
                "Resuming autonomous loop for %s from checkpoint: step=%d crash=%d",
                agent_id, start_iter, crash_count,
            )
        else:
            logger.info("Starting autonomous loop for %s: goal=%s", agent_id, goal)

        for i in range(start_iter, max_iterations):
            # Check for external stop (e.g., via executor.stop_agent)
            if self.registry.get_status(agent_id) == AgentStatus.STOPPED:
                logger.info("Autonomous loop for %s stopped externally", agent_id)
                return

            prompt = f"[Iteration {i + 1}/{max_iterations}] Goal: {goal}"
            try:
                result = await self.invoke(agent_id, prompt)
                crash_count = 0  # reset on successful invoke

                # Persist progress at the iteration boundary so a restart
                # after this point resumes at i+1 rather than replaying.
                self._save_checkpoint(
                    agent_id,
                    step_index=i + 1,
                    max_iterations=max_iterations,
                    crash_count=crash_count,
                    goal=goal,
                    last_output_summary=(result.output or "")[:256] or None,
                )

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
                # Record the crash in the checkpoint so a restart doesn't
                # reset the counter and mask a misbehaving agent.
                self._save_checkpoint(
                    agent_id,
                    step_index=i,
                    max_iterations=max_iterations,
                    crash_count=crash_count,
                    goal=goal,
                )
                if crash_count >= max_crashes:
                    logger.error(
                        "Agent %s quarantined after %d consecutive crashes",
                        agent_id, crash_count,
                    )
                    self.process_table.transition(
                        agent_id,
                        Phase.QUARANTINED,
                        reason=f"{crash_count} consecutive crashes",
                        force=True,
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
            # Terminal success — drop the checkpoint.
            self.checkpoint_store.delete(agent_id)
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
                    # Don't wire execution (scheduler/loops/triggers) for
                    # previously-stopped agents, but DO seed the adapter with
                    # the agent definition so operators can still RUN NOW
                    # from Mission Control. Without this, adapter.invoke
                    # returns "Agent not found" until the operator manually
                    # re-deploys.
                    try:
                        await adapter.create_agent(agent_def)
                        logger.info(
                            "Recovered %s in dormant state (status=%s) — RUN NOW available, scheduler not wired",
                            agent_def.agent_id, prev_status.value,
                        )
                    except Exception:
                        logger.exception(
                            "Dormant create_agent failed for %s", agent_def.agent_id,
                        )
                    skipped += 1
                    continue

            try:
                self._register_process(agent_def)
                self.process_table.transition(agent_def.agent_id, Phase.STARTING)
                await adapter.create_agent(agent_def)
                await self._wire_execution(agent_def)
                self.process_table.transition(agent_def.agent_id, Phase.RUNNING)
                recovered += 1
            except Exception as exc:
                logger.exception("Failed to recover agent %s", agent_def.agent_id)
                self.process_table.transition(
                    agent_def.agent_id,
                    Phase.FAILED,
                    reason=f"recover failed: {exc}",
                    force=True,
                )
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
            candidate = self.agents_root / "clients" / client / agent_def.name
        elif agent_def.ownership == OwnershipType.PERSONAL:
            owner = agent_def.owner_id or "default"
            candidate = self.agents_root / "personal" / owner / agent_def.name
        else:
            candidate = self.agents_root / "shared" / agent_def.name

        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.agents_root.resolve()):
            raise ValueError(f"Agent directory escapes agents root: {resolved}")
        return candidate
