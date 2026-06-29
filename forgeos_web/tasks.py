"""Celery tasks — thin submission/resume/schedule wrappers over the runtime.

Each task does sub-second work (enqueue a continuation / re-enqueue a resume),
then the inner worker pool drives the turns. LLM calls remain non-streaming
(llm_router.chat()). Celery retries ONLY infra errors (broker/DB blips during
submission); agent/LLM failures are terminal business outcomes recorded on the
continuation + agent_runs row, never Celery-retried (that would re-pay tokens).
"""

from __future__ import annotations

import logging

from celery import shared_task

from forgeos_web.db.celery_tenancy import TenantTask

logger = logging.getLogger(__name__)

# Infra-only retry set (import-safe: fall back to a bare tuple if deps absent).
_INFRA_ERRORS: tuple = ()
try:  # pragma: no cover - depends on optional deps being installed
    from django.db.utils import OperationalError

    _INFRA_ERRORS += (OperationalError,)
except Exception:
    pass
try:  # pragma: no cover
    from redis.exceptions import ConnectionError as RedisConnectionError

    _INFRA_ERRORS += (RedisConnectionError,)
except Exception:
    pass


def _json_safe(obj):
    """Coerce a result payload to JSON-serializable (Celery's serializer needs it)."""
    import json
    return json.loads(json.dumps(obj, default=str))


def _serialize_result(agent_id: str, r) -> dict:
    """Flatten an AgentResult into a JSON-safe dict the API/chat views replay."""
    status = r.status.value if hasattr(getattr(r, "status", None), "value") else str(getattr(r, "status", "completed"))
    tcs = []
    for tc in (getattr(r, "tool_calls", None) or []):
        if isinstance(tc, dict):
            tcs.append({"name": tc.get("name"),
                        "input": tc.get("input") or tc.get("arguments") or {},
                        "result": tc.get("result"),
                        "tool_use_id": tc.get("tool_use_id"),
                        "is_error": tc.get("is_error", False)})
        else:
            tcs.append({"name": getattr(tc, "name", None),
                        "input": getattr(tc, "input", None) or getattr(tc, "arguments", None) or {},
                        "result": getattr(tc, "result", None),
                        "tool_use_id": getattr(tc, "tool_use_id", None),
                        "is_error": getattr(tc, "result_is_error", False)})
    meta = getattr(r, "metadata", None) or {}
    return _json_safe({
        "agent_id": agent_id,
        "status": status,
        "output": getattr(r, "output", "") or "",
        "error": getattr(r, "error", None),
        "tokens_used": getattr(r, "tokens_used", 0),
        "tool_calls": tcs,
        "continuation_id": meta.get("continuation_id"),
        "suspend_reason": meta.get("suspend_reason"),
        "pending": meta.get("pending"),
    })


def _execute(agent_id, prompt, context, session_id, tenant_id, trigger):
    """Run the agent IN THE WORKER via platform_executor.invoke and return its
    serialized result. This is the only place agents execute — the web process
    never invokes inline; it enqueues this task."""
    from forgeos_web import di
    from forgeos_web.celery_runtime import run_async

    ctx = di.get_context()
    ex = ctx.platform_executor
    if ex is None:
        raise RuntimeError("platform executor unavailable in worker (FORGEOS_CELERY_BOOT=0?)")

    # Cross-process cache coherence: the platform-api's PUT updates its own
    # in-memory registry cache AND the stack adapter's _agents cache; the
    # worker has its own copies of both that stayed stale until restart, so
    # PUT-then-invoke ran with the old llm_config (e.g. missing api_key_ref/
    # endpoint). Pull the latest definition from Postgres into the registry
    # cache AND propagate it to the adapter (the adapter's invoke() reads
    # from its OWN _agents cache, not the registry — see
    # stacks/forgeos/adapter.py:invoke). One small DB round-trip per
    # invocation; agent definitions are small.
    reg = getattr(ctx, "platform_registry", None)
    if reg is not None and hasattr(reg, "refresh"):
        try:
            fresh = reg.refresh(agent_id)
            if fresh is not None and hasattr(ex, "get_adapter"):
                adapter = ex.get_adapter(fresh.stack)
                if adapter is not None and hasattr(adapter, "_agents"):
                    adapter._agents[agent_id] = fresh
        except Exception:
            logger.warning(
                "registry.refresh(%s) failed; proceeding with cached def", agent_id,
                exc_info=True,
            )
    invoke_ctx = dict(context or {})
    invoke_ctx.setdefault("user_id", "default")
    invoke_ctx["tenant_id"] = tenant_id or invoke_ctx.get("tenant_id", "default")
    invoke_ctx["_trigger"] = trigger
    invoke_ctx.setdefault("_inline", True)  # execute here, don't re-enqueue
    result = run_async(ex.invoke(agent_id, prompt, invoke_ctx, session_id=session_id))
    return _serialize_result(agent_id, result)


@shared_task(name="forgeos.run_agent", base=TenantTask, bind=True,
             autoretry_for=_INFRA_ERRORS, retry_backoff=True, max_retries=3)
def run_agent(self, *, agent_id, prompt="", context=None, session_id=None,
              tenant_id=None, trigger="manual", run_id=None):
    return _execute(agent_id, prompt, context, session_id, tenant_id, trigger)


@shared_task(name="forgeos.run_agent_longrun", base=TenantTask, bind=True,
             autoretry_for=_INFRA_ERRORS, retry_backoff=True, max_retries=3)
def run_agent_longrun(self, *, agent_id, prompt="", context=None, session_id=None,
                      tenant_id=None, trigger="manual", run_id=None):
    # Non-suspendable stacks (CrewAI/ADK/OpenClaw) routed to the agents_longrun
    # queue (large visibility_timeout); same execution path.
    return _execute(agent_id, prompt, context, session_id, tenant_id, trigger)


def _resume_agent_context(cont):
    """Rebuild the per-agent agent_context for a resumed continuation, worker-side.
    Mirror of chat/views.py:_resume_agent_context (kept here to avoid importing the
    web views into the worker — that would be a circular import via celery_app)."""
    from forgeos_web import di

    ctx = di.get_context()
    reg = getattr(ctx, "platform_registry", None)
    ex = getattr(ctx, "platform_executor", None)
    try:
        agent_def = (reg.get(cont.pid) if reg else None)
        if agent_def is None and ex is not None:
            agent_def = ex.registry.get(cont.pid)
        if agent_def is None:
            return None
        from stacks.base import build_agent_context

        return build_agent_context(
            agent_def, agent_def.agent_id,
            context={"user_id": getattr(cont, "user_id", "default")},
        )
    except Exception:
        logger.debug("resume: could not rebuild agent_context for %s", getattr(cont, "pid", "?"))
        return None


@shared_task(name="forgeos.resume_agent", base=TenantTask, bind=True,
             autoretry_for=_INFRA_ERRORS, retry_backoff=True, max_retries=3)
def resume_agent(self, *, external_ref, responded_by=None, tenant_id=None, accept=True):
    """HITL approval/rejection (or long-tool completion) -> DRIVE the parked
    continuation to completion IN THE WORKER and return the continued turn as
    chat events. This is the only place a resume executes — the web process
    enqueues this and replays ``events`` (it never runs ``engine.resume`` inline).

    Mints the approval capability token (so the kernel gate flips ask_human->allow),
    resumes via the suspended continuation's own engine + MCP, then maps the
    RunOutcome to the chat-event contract the dashboard already renders.
    """
    from forgeos_web import di
    from forgeos_web.celery_runtime import run_async
    from src.runtime import Resolution, ResolutionOutcome
    from src.dashboard.chat_events import run_outcome_to_chat_events

    ctx = di.get_context()
    ex = ctx.platform_executor
    if ex is None:
        raise RuntimeError("platform executor unavailable in worker (FORGEOS_CELERY_BOOT=0?)")

    for adapter in getattr(ex, "_adapters", {}).values():
        engine = getattr(adapter, "step_engine", None)
        store = getattr(engine, "_store", None)
        if store is None:
            continue
        cont = store.find_by_external_ref(external_ref)
        if cont is None:
            continue
        rec = next((r for r in cont.pending_calls if r.external_ref == external_ref), None)
        if rec is None:
            continue
        token_id = None
        kernel = getattr(engine, "_kernel", None)
        if accept and kernel is not None and hasattr(kernel, "issue_capability"):
            tok = kernel.issue_capability(
                subject=cont.pid, target=f"tool:{rec.name}", verb="tool.call",
                ttl_seconds=3600,
                metadata={"external_ref": external_ref, "continuation_id": cont.continuation_id},
            )
            token_id = tok.id
        resolution = Resolution(
            continuation_id=cont.continuation_id, tool_use_id=rec.tool_use_id,
            outcome=ResolutionOutcome.ACCEPT if accept else ResolutionOutcome.REJECT,
            capability_token=token_id, responded_by=responded_by,
        )
        outcome = run_async(engine.resume(
            resolution,
            tool_executor=getattr(adapter, "_tool_executor", None),
            agent_context=_resume_agent_context(cont),
        ))
        return _json_safe({
            "resumed": external_ref,
            "continuation_id": cont.continuation_id,
            "events": run_outcome_to_chat_events(outcome),
            "output": getattr(outcome, "output", None),
            "tokens_used": getattr(outcome, "tokens_used", 0) or 0,
        })
    return {"resumed": None, "continuation_id": None, "events": [],
            "error": f"no suspended continuation for ref {external_ref}"}


@shared_task(name="forgeos.scheduled_tick", base=TenantTask, bind=True,
             autoretry_for=_INFRA_ERRORS, retry_backoff=True, max_retries=3)
def scheduled_tick(self, *, agent_id, tenant_id=None):
    """Beat-fired periodic run for a SCHEDULED agent."""
    return _execute(agent_id, "", None, None, tenant_id, "cron")


@shared_task(name="forgeos.evict_stale_sessions")
def evict_stale_sessions():
    """Beat-fired maintenance — replaces the FastAPI in-process eviction loop
    (fastapi_app.py:_evict_stale_sessions). Purges expired chat sessions from
    the shared session store.

    TODO(cutover): wire to the Redis-backed session store once chat sessions
    move off the per-process dict (chat/views.py:_chat_sessions). Until then this
    is a no-op placeholder so the Beat schedule + worker wiring is in place.
    """
    purged = 0
    logger.debug("evict_stale_sessions tick (purged=%d)", purged)
    return {"purged": purged}
