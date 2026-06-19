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


def _enqueue(agent_id: str, prompt: str, context: dict | None, run_id, tenant_id, trigger: str):
    from forgeos_web.celery_runtime import get_runtime_service, run_async

    rt = get_runtime_service()
    agent_def = rt._registry.get(agent_id)
    if agent_def is None:
        raise ValueError(f"unknown agent_id: {agent_id}")
    ctx = dict(context or {})
    ctx.update(run_id=run_id, tenant_id=tenant_id, _trigger=trigger)
    cont_id = run_async(rt.enqueue_invoke(agent_def, prompt, ctx))
    return {"run_id": run_id, "continuation_id": cont_id, "status": "enqueued"}


@shared_task(name="forgeos.run_agent", base=TenantTask, bind=True,
             autoretry_for=_INFRA_ERRORS, retry_backoff=True, max_retries=3)
def run_agent(self, *, agent_id, prompt, context=None, run_id=None, tenant_id=None, trigger="manual"):
    return _enqueue(agent_id, prompt, context, run_id, tenant_id, trigger)


@shared_task(name="forgeos.run_agent_longrun", base=TenantTask, bind=True,
             autoretry_for=_INFRA_ERRORS, retry_backoff=True, max_retries=3)
def run_agent_longrun(self, *, agent_id, prompt, context=None, run_id=None, tenant_id=None, trigger="manual"):
    # Non-suspendable stacks (CrewAI/ADK/OpenClaw): whole loop runs in one worker
    # turn. Routed to the agents_longrun queue (large visibility_timeout).
    return _enqueue(agent_id, prompt, context, run_id, tenant_id, trigger)


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
    """Beat-fired periodic run for a SCHEDULED agent (durable invoke path)."""
    return _enqueue(agent_id, "", None, None, tenant_id, trigger="cron")


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
