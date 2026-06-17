"""
RuntimeService — the worker tier wired as one bootstrappable subsystem.

Bundles the durable store, ledger, runnable queue, enqueuer, step engine,
resume service, and a pool of stateless workers. Bootstrap builds one of these
(behind FORGEOS_RUNTIME_WORKERS) and binds its engine to the Helios OS adapter so
invokes ENQUEUE a continuation instead of running inline — the worker pool then
drives it off the queue, parks on ask_human (freeing the worker), and the
resume service re-enqueues it on approval.

Backends are chosen from the environment:
  * queue  — Redis Streams (REDIS_URL) else in-memory
  * ledger — Postgres (DATABASE_URL) else in-memory
  * store  — passed in (Postgres when DATABASE_URL, else memory/sqlite)
"""

from __future__ import annotations

import asyncio
import logging
import os

from src.runtime.enqueuer import Enqueuer, priority_for
from src.runtime.engine import StepEngine
from src.runtime.ledger import InMemoryLedger
from src.runtime.queue import InMemoryRunnableQueue
from src.runtime.resume_service import ResumeService
from src.runtime.worker import Worker

logger = logging.getLogger(__name__)


def build_queue():
    """Redis Streams queue when REDIS_URL is set, else in-memory."""
    url = os.environ.get("REDIS_URL")
    if url:
        try:
            import redis.asyncio as aioredis

            from src.runtime.queue import RedisRunnableQueue
            # Long-lived worker connections go stale between claims (the pool
            # idles, then a read on a dead socket times out — seen as repeated
            # "Timeout reading from <host>" / CancelledError storms that can
            # interrupt a resume mid-flight). Keep them alive + auto-retry so the
            # worker tier (and the durable HITL resume) stays healthy across idle.
            client = aioredis.from_url(
                url,
                socket_keepalive=True,
                health_check_interval=30,
                retry_on_timeout=True,
                socket_timeout=60,
                socket_connect_timeout=10,
            )
            # Unique consumer name per process. Redis Streams splits a group's
            # entries across DISTINCT consumer names; if every process used the
            # same name ("w0"), multiple servers/pods (or a stray leftover
            # instance) sharing one Redis would steal each other's tasks
            # non-deterministically. host+pid keeps each process its own
            # consumer; orphaned entries from a dead consumer are recovered by
            # the XAUTOCLAIM reclaim path in claim().
            import socket as _socket
            consumer = f"w-{_socket.gethostname()}-{os.getpid()}"
            logger.info("runtime workers: using Redis Streams queue (%s) consumer=%s", url, consumer)
            return RedisRunnableQueue(client, consumer=consumer), "redis"
        except Exception:
            logger.exception("runtime workers: Redis queue unavailable; using in-memory")
    return InMemoryRunnableQueue(), "memory"


def build_ledger(db=None):
    """Postgres ledger when a connected db is given, else in-memory."""
    if db is not None and getattr(db, "is_connected", False):
        from src.runtime.ledger import PostgresLedger
        logger.info("runtime workers: using Postgres runnable_ledger")
        return PostgresLedger(db)
    return InMemoryLedger()


class RuntimeService:
    """Owns the engine + worker pool. ``enqueue_invoke`` turns an agent
    invocation into a queued continuation; the workers drive it."""

    def __init__(
        self,
        *,
        kernel,
        llm_router,
        tool_executor,
        registry,
        store,
        ledger=None,
        queue=None,
        db=None,
        workers: int = 4,
        lease_s: float = 600.0,
    ) -> None:
        # single_step=True: one LLM turn per worker claim. The worker re-enqueues
        # the continuation after each turn (one turn == one runnable Redis task).
        self.engine = StepEngine(llm_router=llm_router, kernel=kernel, store=store,
                                 single_step=True)
        self.store = store
        self.ledger = ledger or build_ledger(db)
        self.queue = queue or build_queue()[0]
        self.enqueuer = Enqueuer(store=store, ledger=self.ledger, queue=self.queue)
        self.resume = ResumeService(store=store, enqueuer=self.enqueuer, kernel=kernel)
        self._registry = registry
        self._tool_executor = tool_executor
        self._n = max(1, workers)
        self._worker = Worker(
            engine=self.engine, queue=self.queue, ledger=self.ledger,
            enqueuer=self.enqueuer, lease_s=lease_s, context_builder=self._context_for,
        )
        self._tasks: list[asyncio.Task] = []
        self._stop = False

    # -- per-continuation tool context ------------------------------------

    def _context_for(self, cont_id: str):
        """Resolve (tool_executor, agent_context) for the continuation's agent."""
        cont = self.store.load(cont_id)
        agent_def = self._registry.get(cont.pid) if (cont and self._registry) else None
        ctx: dict = {"agent_id": cont.pid if cont else "", "namespace": getattr(cont, "namespace", "default")}
        if agent_def is not None:
            try:
                from stacks.base import build_agent_context
                # Carry the continuation's acting user so per-user credentials +
                # MCP routing apply on worker-tier (durable) runs too.
                cont_ctx = {"user_id": getattr(cont, "user_id", "default")} if cont else None
                ctx = build_agent_context(agent_def, agent_def.agent_id, context=cont_ctx)
            except Exception:
                logger.debug("build_agent_context failed; using minimal context")
        return self._tool_executor, ctx

    # -- enqueue an invocation --------------------------------------------

    async def enqueue_invoke(self, agent_def, prompt: str, context: dict | None = None) -> str:
        """Create a continuation from an agent + prompt and enqueue it.
        Returns the run handle (continuation id). Workers drive it."""
        from src.platform.agentic_loop import build_tool_definitions, append_client_mcp_tools
        from src.runtime.shaping import extract_chat_history
        from stacks.base import build_agent_context

        ctx = context or {}
        tools = build_tool_definitions(self._tool_executor, agent_def.tools or None)
        # Merge the acting user's per-user MCP tool schemas (e.g. their JIRA via
        # mcp-atlassian) so the LLM sees them. client_id is derived the same way
        # the run-time agent_context derives it (user:<user_id> for opted agents).
        _client_id = build_agent_context(agent_def, agent_def.agent_id, context=ctx).get("client_id")
        tools = await append_client_mcp_tools(tools, self._tool_executor, _client_id, agent_def.tools or None)
        system = agent_def.system_prompt or f"You are {agent_def.name}. {getattr(agent_def, 'description', '')}"

        # Cross-turn chat memory: when a session_id is present, re-seed this
        # turn's continuation with the prior conversation. Each chat turn is its
        # own continuation (one turn == one runnable Redis task), so memory is
        # carried by re-loading the previous DONE continuation for the session.
        session_id = ctx.get("session_id")
        history = None
        if session_id:
            try:
                prev = self.store.load_latest_for_session(session_id, status="done")
                if prev is not None:
                    history = extract_chat_history(prev.messages) or None
            except Exception:
                logger.debug("session history lookup failed for %s", session_id)

        cont = self.engine.create_continuation(
            pid=agent_def.agent_id,
            system_prompt=system,
            user_prompt=prompt,
            provider=agent_def.llm_config.provider,
            chat_model=agent_def.llm_config.chat_model,
            # Carry the per-agent gateway routing onto the continuation so the
            # worker's LLMConfig reaches the right endpoint with the right key
            # (without these, atlas/qwen agents fall back to [Simulated …]).
            endpoint=agent_def.llm_config.endpoint,
            api_key_ref=agent_def.llm_config.api_key_ref,
            tools=tools or None,
            session_id=session_id,
            history=history,
            context=context,
            goal=getattr(agent_def, "goal", None) or None,
            tenant_id=ctx.get("tenant_id", (agent_def.metadata or {}).get("tenant_id", "default")),
            namespace=getattr(agent_def, "namespace", "default"),
            source=ctx.get("_trigger", "manual"),
        )
        await self.enqueuer.enqueue_runnable(
            cont.continuation_id, priority=priority_for(cont.source),
        )
        logger.info("runtime workers: enqueued run %s for %s", cont.continuation_id, agent_def.agent_id)
        return cont.continuation_id

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._tasks:
            return
        # Rebuild the queue from the durable ledger (recover after a restart /
        # Redis flush) before workers start claiming.
        try:
            n = await self.enqueuer.rebuild_from_ledger()
            if n:
                logger.info("runtime workers: re-enqueued %d runnable items from ledger", n)
        except Exception:
            logger.exception("runtime workers: ledger rebuild failed")
        for i in range(self._n):
            self._tasks.append(asyncio.create_task(self._loop(i), name=f"forgeos-worker-{i}"))
        logger.info("runtime workers: started %d worker(s)", self._n)

    async def _loop(self, idx: int) -> None:
        while not self._stop:
            try:
                results = await self._worker.run_once(count=2)
            except Exception:
                logger.exception("runtime worker %d loop error", idx)
                results = []
            if not results:
                await asyncio.sleep(0.5)  # idle backoff between claims

    async def stop(self) -> None:
        self._stop = True
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        logger.info("runtime workers: stopped")


__all__ = ["RuntimeService", "build_ledger", "build_queue"]
