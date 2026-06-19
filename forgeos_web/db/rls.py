"""Connection-level RLS control: set ``app.current_tenant`` per request/task.

Mirrors src/core/database.py:117-148 (``db.tenant()`` / ``db.admin()``). The
session var is set with ``set_config(..., is_local := true)`` — TRANSACTION-scoped
— which is mandatory for safety under pgbouncer/Cloud SQL transaction pooling:
the setting auto-clears at COMMIT/ROLLBACK and can never leak into the next
checkout of a pooled server connection.

Correctness requires the SET and the queries that read it to share one
transaction. The web path gets this from ``ATOMIC_REQUESTS=True``; Celery tasks
wrap their body in ``transaction.atomic()`` (see db/celery_tenancy.py).
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager

from django.db import connections

# The active tenant for the current logical context. A ContextVar (not thread
# local) so it is correct under async views and gevent/eventlet Celery pools.
_tenant_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "forgeos_current_tenant", default=None
)

_SET_SQL = "SELECT set_config('app.current_tenant', %s, true)"


def current_tenant() -> str | None:
    """The tenant bound to the current context, or None in admin/cross-tenant."""
    return _tenant_var.get()


def set_tenant(tenant_id: str | None, *, using: str = "default") -> None:
    """Set ``app.current_tenant`` on the ``using`` connection (Postgres only).

    Empty/None tenant => empty string, which never equals any ``tenant_id`` and
    so yields zero rows on RLS tables (the ``db.admin()`` cross-tenant case).
    No-op on non-Postgres backends (sqlite in unit tests / manage.py check).
    """
    conn = connections[using]
    if conn.vendor != "postgresql":
        return
    with conn.cursor() as cur:
        cur.execute(_SET_SQL, [tenant_id or ""])


def reset_tenant(*, using: str = "default") -> None:
    """Clear the tenant (admin context). With is_local=true this also happens
    automatically at transaction end; called explicitly for long-lived conns."""
    set_tenant("", using=using)


@contextmanager
def tenant_context(tenant_id: str | None, *, using: str = "default"):
    """Bind a tenant for a block of work (the Django analogue of db.tenant()).

    Sets both the contextvar (so TenantManager scopes queries) and the DB
    session var (so RLS enforces). Use around each DB-touching step in code that
    runs outside an atomic request (SSE views, multi-transaction tasks).
    """
    token = _tenant_var.set(tenant_id)
    set_tenant(tenant_id, using=using)
    try:
        yield
    finally:
        reset_tenant(using=using)
        _tenant_var.reset(token)


def bind_var(tenant_id: str | None):
    """Set only the contextvar (no DB call). Returns the reset token. Used by
    middleware/Celery signals that issue ``set_tenant`` separately inside the
    request/task transaction."""
    return _tenant_var.set(tenant_id)


def reset_var(token) -> None:
    _tenant_var.reset(token)
