"""Tenant binding for Celery tasks (wired in Workstream C / Step 6).

Every task that touches tenant data must run with ``app.current_tenant`` set.
Tenant is passed explicitly as the ``tenant_id`` task kwarg (never inherited
across the broker). Because ``set_config(local)`` only lives inside a
transaction, the task body opens one via ``TenantTask`` and sets the tenant
inside it — safe under transaction pooling.

Cross-tenant maintenance tasks (ledger sweeps, lease recovery) pass
``tenant_id=None`` and operate via ``Model.all_objects`` / the no-RLS infra
tables.
"""

from __future__ import annotations

try:
    import celery
except ImportError:  # celery is an optional dep; this module is import-safe
    celery = None  # type: ignore

from django.db import transaction

from .rls import bind_var, reset_var, set_tenant


def install_tenant_signals() -> None:
    """Connect prerun/postrun signals that manage the tenant contextvar.

    Call once from the Celery app module. The DB session var itself is set in
    ``TenantTask.__call__`` (inside the task's transaction), not here.
    """
    if celery is None:
        return
    from celery.signals import task_postrun, task_prerun

    @task_prerun.connect
    def _bind(task_id=None, task=None, args=None, kwargs=None, **_):
        tid = (kwargs or {}).get("tenant_id")
        if task is not None:
            task.request._forgeos_tenant_token = bind_var(tid)

    @task_postrun.connect
    def _unbind(task_id=None, task=None, **_):
        token = getattr(getattr(task, "request", None), "_forgeos_tenant_token", None)
        if token is not None:
            reset_var(token)


if celery is not None:

    class TenantTask(celery.Task):  # type: ignore[misc]
        """Base task that runs its body inside a transaction with the tenant set."""

        def __call__(self, *args, **kwargs):
            tid = kwargs.get("tenant_id")
            with transaction.atomic():
                set_tenant(tid)
                return super().__call__(*args, **kwargs)
else:  # pragma: no cover - celery not installed
    TenantTask = object  # type: ignore
