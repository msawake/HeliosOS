"""
ForgeOS Native Stack Adapter.

Wraps the existing AgentInvoker / hook chain / tool executor into the
AgentStackAdapter interface. This is the built-in "simple" stack that
uses the platform's own LLM client and tool system directly.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
from typing import Any

from stacks.base import (
    AgentDefinition,
    AgentResult,
    AgentStackAdapter,
    AgentStatus,
    ExecutionType,
    OwnershipType,
    build_agent_context,
)

logger = logging.getLogger(__name__)


class ForgeOSAdapter(AgentStackAdapter):
    stack_name = "forgeos"
    supports_suspend = True  # platform owns the loop -> durable ask_human

    def __init__(self, llm_router=None, tool_executor=None, kernel=None):
        self._llm_router = llm_router
        self._tool_executor = tool_executor
        self._kernel = kernel  # optional; falls back to the SDK runtime singleton
        self._agents: dict[str, AgentDefinition] = {}
        self._loops: dict[str, asyncio.Task] = {}
        self._step_engine = None  # lazily built when runtime-v2 is enabled
        self._runtime_service = None  # set by bootstrap when the worker tier is on

    # -- runtime-v2 (durable continuation engine) --------------------------

    @staticmethod
    def _runtime_v2_enabled() -> bool:
        import os
        # On by default — suspendable runs route through the durable StepEngine
        # so per-tool human approvals (governance.approvals) actually park-and-
        # resume. Set FORGEOS_RUNTIME_V2=0 (false/no/off) for the legacy loop.
        return os.environ.get("FORGEOS_RUNTIME_V2", "1").strip().lower() not in ("0", "false", "no", "off")

    def _resolve_kernel(self):
        """The kernel exposing .syscall — explicit param first, else the
        bootstrap-registered SDK runtime singleton."""
        if self._kernel is not None:
            return self._kernel
        try:
            from src.forgeos_sdk.runtime import runtime as _rt
            if _rt.is_registered:
                return _rt._kernel
        except Exception:
            pass
        return None

    def _get_step_engine(self, kernel):
        """Lazily build the StepEngine. Uses a durable SQLite continuation store
        when FORGEOS_RUNTIME_DB is set, else an in-process store."""
        if self._step_engine is not None:
            return self._step_engine
        import os

        from src.runtime import MemoryContinuationStore, StepEngine

        store = None
        # Production: when DATABASE_URL is set, persist continuations in Postgres
        # (migration 013) so suspend/resume survives a platform restart.
        if os.environ.get("DATABASE_URL"):
            try:
                from src.core.database import create_database_client
                from src.runtime import PostgresContinuationStore
                _db = create_database_client()
                if getattr(_db, "is_connected", False):
                    store = PostgresContinuationStore(_db)
                    logger.info("runtime-v2: continuations persisted in Postgres")
            except Exception:
                logger.exception("runtime-v2: Postgres continuation store unavailable; falling back")
        # Dev durable: a SQLite file (survives restart, no DB needed).
        if store is None and os.environ.get("FORGEOS_RUNTIME_DB"):
            from src.runtime import SqliteContinuationStore
            store = SqliteContinuationStore(os.environ["FORGEOS_RUNTIME_DB"])
        # Default: in-process (ephemeral).
        if store is None:
            store = MemoryContinuationStore()
        self._step_engine = StepEngine(llm_router=self._llm_router, kernel=kernel, store=store)
        return self._step_engine

    @property
    def step_engine(self):
        """The StepEngine if runtime-v2 has been used (else None). For resume."""
        return self._step_engine

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def
        logger.info("ForgeOS agent created: %s (%s)", agent_def.name, agent_def.agent_id)
        return agent_def.agent_id

    async def _forward_to_pod(self, agent_id: str, pod_url: str, prompt: str) -> AgentResult:
        """Forward an invoke to a pod-backed agent's HTTP /invoke (P1.7)."""
        import time

        import httpx

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(f"{pod_url.rstrip('/')}/invoke", json={"prompt": prompt})
            resp.raise_for_status()
            data = resp.json()
            ok = data.get("status") == "completed"
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.COMPLETED if ok else AgentStatus.FAILED,
                output=data.get("output", ""),
                error=None if ok else data.get("error", "pod returned non-completed status"),
                elapsed_ms=(time.time() - start) * 1000,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("pod forward failed for %s -> %s: %s", agent_id, pod_url, e)
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED,
                error=f"pod forward failed: {e}", elapsed_ms=(time.time() - start) * 1000,
            )

    async def invoke(
        self, agent_id: str, prompt: str, context: dict | None = None,
        history: list[dict] | None = None,
    ) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        # P1.7 — pod-backed dispatch. If the agent is bound to a pod's HTTP
        # service (metadata.pod_service_url), forward the invoke there instead of
        # running the loop in-process. Lets `forgeos invoke <id>` execute IN the
        # agent's Kubernetes pod. On GKE the URL is the in-cluster Service DNS;
        # locally it's a kubectl port-forward to the pod's Service.
        pod_url = (agent_def.metadata or {}).get("pod_service_url")
        if pod_url:
            return await self._forward_to_pod(agent_id, str(pod_url), prompt)

        if self._llm_router:
            if not self._tool_executor:
                logger.warning("ForgeOS invoke for %s: no tool_executor — agent will run without tools", agent_id)
            from src.platform.agentic_loop import (
                run_agentic_loop, build_tool_definitions, append_client_mcp_tools,
            )
            # LLM-routing keys live in agent_def.metadata (where the YAML/PUT
            # endpoint stores them), but llm_router.chat() reads them from
            # llm_config.metadata. Mirror the relevant ones across so per-agent
            # base_url / fallback_provider actually take effect.
            for k in ("base_url", "fallback_provider", "api_key_ref"):
                v = (agent_def.metadata or {}).get(k)
                if v and not agent_def.llm_config.metadata.get(k):
                    agent_def.llm_config.metadata[k] = v
            tools = build_tool_definitions(self._tool_executor, agent_def.tools or None)
            # Merge the acting user's per-user MCP tool schemas (e.g. their JIRA
            # via mcp-atlassian) so the LLM sees them on the inline path too.
            try:
                _cid = build_agent_context(agent_def, agent_id, context=context).get("client_id")
                tools = await append_client_mcp_tools(
                    tools, self._tool_executor, _cid, agent_def.tools or None,
                )
            except Exception:
                logger.debug("client MCP tool merge failed for %s", agent_id, exc_info=True)
            # Warn if agent expects MCP tools but none were resolved
            if agent_def.tools and not tools:
                logger.warning(
                    "Agent %s has %d configured tools but none resolved — MCP servers may not be connected",
                    agent_id, len(agent_def.tools),
                )
            system = agent_def.system_prompt or f"You are {agent_def.name}. {agent_def.description}"
            cb_registry = (context or {}).get("_callback_registry")

            # Runtime-v2 (opt-in): route suspendable runs through the durable
            # StepEngine so an ask_human parks the run instead of erroring. Falls
            # straight back to the legacy loop when the flag is off or no kernel
            # is wired — fully additive.
            if self._runtime_v2_enabled():
                kernel = self._resolve_kernel()
                if kernel is not None:
                    return await self._invoke_via_engine(
                        agent_def, agent_id, prompt, tools, system, context, history, kernel,
                    )

            result = await run_agentic_loop(
                llm_router=self._llm_router,
                llm_config=agent_def.llm_config,
                system_prompt=system,
                user_prompt=prompt,
                tool_definitions=tools or None,
                tool_executor=self._tool_executor,
                agent_context=build_agent_context(
                    agent_def, agent_id,
                    invocation_id=(context or {}).get("invocation_id"),
                    context=context,
                ),
                context=context,
                history=history,
                goal=agent_def.goal,
                callback_registry=cb_registry,
            )
            result.agent_id = agent_id
            return result

        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.COMPLETED,
            output=f"[SIMULATED - No LLM API key configured] Agent '{agent_def.name}' received: {prompt[:100]}. "
                   f"Configure ANTHROPIC_API_KEY or OPENAI_API_KEY in .env for real LLM execution.",
            error="No LLM provider available. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.",
        )

    async def _invoke_via_engine(
        self, agent_def, agent_id, prompt, tools, system, context, history, kernel,
    ) -> AgentResult:
        """Run via the durable StepEngine and map RunOutcome -> AgentResult.

        A SUSPENDED outcome is NOT a failure: the run parked on human approval
        (or an external wait). It maps to PAUSED with the continuation id +
        pending approvals in ``metadata`` so the resume service / dashboard can
        approve it later. The worker is freed immediately.
        """
        from src.runtime import RunStatus

        # Worker tier: when the bootstrap wired a RuntimeService, ENQUEUE the
        # run instead of driving it inline. The worker pool claims it off the
        # (Redis Streams / in-memory) queue and drives it; we return the run
        # handle immediately so no request is held across the agentic loop.
        # ``_inline`` forces synchronous execution (e.g. interactive chat needs
        # the agent's reply text, not a queued handle).
        if self._runtime_service is not None and not (context or {}).get("_inline"):
            run_id = await self._runtime_service.enqueue_invoke(agent_def, prompt, context)
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.RUNNING,
                output="",
                metadata={"continuation_id": run_id, "run_id": run_id, "queued": True},
            )

        engine = self._get_step_engine(kernel)
        ctx = context or {}
        outcome = await engine.run(
            pid=agent_id,
            system_prompt=system,
            user_prompt=prompt,
            provider=agent_def.llm_config.provider,
            chat_model=agent_def.llm_config.chat_model,
            endpoint=agent_def.llm_config.endpoint,
            api_key_ref=agent_def.llm_config.api_key_ref,
            tools=tools or None,
            tool_executor=self._tool_executor,
            agent_context=build_agent_context(
                agent_def, agent_id, invocation_id=ctx.get("invocation_id"),
                context=context,
            ),
            history=history,
            context=context,
            goal=agent_def.goal or None,
            tenant_id=ctx.get("tenant_id", "default"),
            namespace=getattr(agent_def, "namespace", "default"),
            source=ctx.get("_trigger", "manual"),
        )
        if outcome.status is RunStatus.SUSPENDED:
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.PAUSED,
                output="",
                tokens_used=outcome.tokens_used,
                metadata={
                    "continuation_id": outcome.continuation_id,
                    "suspend_reason": outcome.suspend_reason,
                    "pending": outcome.pending,
                },
            )
        if outcome.status is RunStatus.FAILED:
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED, error=outcome.error,
                metadata={"continuation_id": outcome.continuation_id},
            )
        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.COMPLETED,
            output=outcome.output,
            tokens_used=outcome.tokens_used,
            # Surface the engine's run rollup so the agent_runs row records the
            # real tool-call count, token split, and model (not 0/0/null).
            tool_calls=[{}] * (outcome.tool_calls or 0),
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            model=outcome.model,
            metadata={"continuation_id": outcome.continuation_id},
        )

    async def start_loop(self, agent_id: str) -> None:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return

        async def _loop():
            interval = agent_def.metadata.get("loop_interval_seconds", 60)
            while True:
                try:
                    await self.invoke(agent_id, f"Standing duties for {agent_def.name}")
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("ForgeOS loop error for %s", agent_id)
                await asyncio.sleep(interval)

        self._loops[agent_id] = asyncio.create_task(_loop(), name=f"forgeos-loop-{agent_id}")
        logger.info("Started ForgeOS loop for %s", agent_id)

    async def stop(self, agent_id: str) -> None:
        task = self._loops.pop(agent_id, None)
        if task:
            task.cancel()
        logger.info("Stopped ForgeOS agent %s", agent_id)

    def get_status(self, agent_id: str) -> AgentStatus:
        if agent_id in self._loops and not self._loops[agent_id].done():
            return AgentStatus.RUNNING
        if agent_id in self._agents:
            return AgentStatus.IDLE
        return AgentStatus.STOPPED

    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]:
        return {
            "agent.py": textwrap.dedent(f"""\
                \"\"\"
                ForgeOS Agent: {agent_def.name}
                Stack: forgeos | Type: {agent_def.execution_type.value}
                \"\"\"
                from stacks.base import AgentDefinition, ExecutionType, OwnershipType, LLMConfig

                AGENT_DEF = AgentDefinition(
                    name="{agent_def.name}",
                    stack="forgeos",
                    execution_type=ExecutionType.{agent_def.execution_type.name},
                    ownership=OwnershipType.{agent_def.ownership.name},
                    description="{agent_def.description}",
                    tools={agent_def.tools!r},
                    llm_config=LLMConfig(
                        chat_model="{agent_def.llm_config.chat_model}",
                        reasoning_model={agent_def.llm_config.reasoning_model!r},
                        provider="{agent_def.llm_config.provider}",
                    ),
                )
            """),
            "tools.py": textwrap.dedent(f"""\
                \"\"\"MCP-wrapped tools for {agent_def.name}.\"\"\"

                TOOL_DEFINITIONS = {agent_def.tools!r}
            """),
            "prompts/system.md": textwrap.dedent(f"""\
                # {agent_def.name}

                You are {agent_def.name}, a ForgeOS agent.

                ## Role
                {agent_def.description or 'General-purpose assistant.'}

                ## Rules
                - Always think step-by-step
                - Use available MCP tools when needed
                - Report progress clearly
            """),
            "config.yaml": textwrap.dedent(f"""\
                name: "{agent_def.name}"
                stack: forgeos
                execution_type: {agent_def.execution_type.value}
                ownership: {agent_def.ownership.value}
                llm:
                  chat_model: "{agent_def.llm_config.chat_model}"
                  reasoning_model: {agent_def.llm_config.reasoning_model or 'null'}
                  provider: "{agent_def.llm_config.provider}"
                tools: {agent_def.tools!r}
            """),
        }
