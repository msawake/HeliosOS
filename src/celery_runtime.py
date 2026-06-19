"""Async bridge + per-worker RuntimeService for Celery.

The existing runtime (StepEngine, RedisRunnableQueue, MCP clients) is asyncio,
but Celery task bodies are sync. We run ONE long-lived event loop per worker
process and marshal coroutines onto it with ``run_coroutine_threadsafe`` — never
``asyncio.run()`` per task (that would tear down the redis.asyncio pool + MCP
clients every call, the exact stale-socket failure the runtime guards against).

``get_runtime_service`` returns the process's RuntimeService — preferring one
already built by boot (di.AppContext.runtime_service), else assembling it from
the context's platform singletons — and starts its worker loop on the bridge
loop so claimed continuations are driven turn-by-turn.
"""

from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()
_runtime_service = None
_rt_lock = threading.Lock()


def worker_loop() -> asyncio.AbstractEventLoop:
    """The process-wide background event loop (created on first use)."""
    global _loop
    if _loop is None:
        with _loop_lock:
            if _loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(
                    target=loop.run_forever, daemon=True, name="forgeos-celery-loop"
                ).start()
                _loop = loop
    return _loop


def run_async(coro):
    """Run a coroutine on the background loop and block for its result."""
    return asyncio.run_coroutine_threadsafe(coro, worker_loop()).result()


def _build_store(db):
    if db is not None and getattr(db, "is_connected", False):
        from src.runtime import PostgresContinuationStore

        return PostgresContinuationStore(db)
    from src.runtime import MemoryContinuationStore

    return MemoryContinuationStore()


def get_runtime_service():
    """Return (building if needed) this worker's RuntimeService."""
    global _runtime_service
    if _runtime_service is not None:
        return _runtime_service
    with _rt_lock:
        if _runtime_service is None:
            from src.forgeos_web import di

            ctx = di.get_context()  # raises if boot hasn't populated the context
            if ctx.runtime_service is not None:
                _runtime_service = ctx.runtime_service
            else:
                from src.runtime import RuntimeService

                _runtime_service = RuntimeService(
                    kernel=ctx.kernel,
                    llm_router=ctx.llm_router,
                    tool_executor=ctx.tool_executor,
                    registry=ctx.platform_registry,
                    store=_build_store(ctx.db_client),
                    db=ctx.db_client,
                )
            # Drive turns for claimed continuations on the bridge loop.
            run_async(_runtime_service.start())
    return _runtime_service


def shutdown() -> None:
    """Drain the runtime + stop the loop (worker_process_shutdown)."""
    global _runtime_service, _loop
    if _runtime_service is not None:
        try:
            run_async(_runtime_service.stop())
        except Exception:
            logger.exception("runtime stop failed during worker shutdown")
        _runtime_service = None
    if _loop is not None:
        _loop.call_soon_threadsafe(_loop.stop)
        _loop = None
