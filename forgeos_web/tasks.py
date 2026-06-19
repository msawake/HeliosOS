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
                        "result": tc.get("result")})
        else:
            tcs.append({"name": getattr(tc, "name", None),
                        "input": getattr(tc, "input", None) or getattr(tc, "arguments", None) or {},
                        "result": getattr(tc, "result", None)})
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


@shared_task(name="forgeos.resume_agent", base=TenantTask, bind=True,
             autoretry_for=_INFRA_ERRORS, retry_backoff=True, max_retries=3)
def resume_agent(self, *, external_ref, responded_by=None, tenant_id=None):
    """HITL approval / long-tool completion -> re-enqueue the parked continuation."""
    from forgeos_web.celery_runtime import get_runtime_service, run_async

    rt = get_runtime_service()
    cont_id = run_async(rt.resume.approve(external_ref, responded_by=responded_by))
    return {"resumed": external_ref, "continuation_id": cont_id}


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
