"""Process-global application context for the Django web layer.

FastAPI used a factory (`create_fastapi_app(...)` in src/dashboard/fastapi_app.py)
that received ~20 assembled platform objects as keyword arguments. Django has no
per-app-instance dependency injection, so the equivalent wiring lives here: a
single process-global ``AppContext`` populated once at boot (or lazily on first
access) and read by views, DRF auth classes, and Celery tasks.

This is the migration seam. During the strangler phase it is populated from the
existing ``PlatformBootstrap`` (see ``populate_from_bootstrap``); after cutover the
same objects are built directly in ``AppConfig.ready()`` / Celery worker init.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Holds the platform singletons the web layer dispatches into.

    Field names mirror the kwargs of ``create_fastapi_app`` (bootstrap.py:992)
    so the port is a mechanical 1:1 mapping. Everything is ``Any`` to avoid a
    hard import cycle with the platform packages at module-import time.
    """

    runtime_service: Any = None
    company_system: Any = None
    workflow_engine: Any = None
    company_name: str = "AI Company"
    db_client: Any = None
    auth_enabled: bool = True
    platform_executor: Any = None
    platform_registry: Any = None
    llm_router: Any = None
    admin_tools: Any = None
    admin_invoker: Any = None
    admin_registry: Any = None
    ontology: Any = None
    tenant_id: str = "default"
    kernel: Any = None
    credential_store: Any = None
    environment_manager: Any = None
    env_def_store: Any = None
    env_service: Any = None
    mcp_manager: Any = None
    tool_executor: Any = None
    stripe_billing: Any = None
    # Populated marker so callers can assert boot ordering.
    extras: dict[str, Any] = field(default_factory=dict)


_ctx: AppContext | None = None
_lock = threading.Lock()


def set_context(ctx: AppContext) -> None:
    """Install the process-global context. Idempotent-by-replacement."""
    global _ctx
    with _lock:
        _ctx = ctx
    logger.info("AppContext installed (executor=%s, runtime=%s, db=%s)",
                bool(ctx.platform_executor), bool(ctx.runtime_service),
                bool(ctx.db_client))


def get_context() -> AppContext:
    """Return the installed context, or raise if accessed before boot."""
    if _ctx is None:
        raise RuntimeError(
            "AppContext not initialized. Call di.set_context(...) during boot "
            "(populate_from_bootstrap) before serving requests."
        )
    return _ctx


def try_get_context() -> AppContext | None:
    """Non-raising accessor for code paths that must tolerate pre-boot state."""
    return _ctx


def populate_from_bootstrap(boot: Any, *, auth_enabled: bool = True) -> AppContext:
    """Build an AppContext from a live ``PlatformBootstrap`` instance.

    Mirrors ``PlatformBootstrap.create_api_app`` (bootstrap.py:987-1015): the same
    objects that were passed to the FastAPI factory are gathered here instead.
    """
    runtime_service = boot._maybe_build_runtime_service()
    company_name = boot.config.get("company", {}).get("name", "AI Company")
    ctx = AppContext(
        runtime_service=runtime_service,
        company_system=boot.system,
        workflow_engine=boot.workflow_engine,
        company_name=company_name,
        db_client=boot._db,
        auth_enabled=auth_enabled,
        platform_executor=boot.executor,
        platform_registry=boot.platform_registry,
        llm_router=boot.llm_router,
        admin_tools=getattr(boot, "admin_tools", None),
        admin_invoker=boot.legacy_invoker,
        admin_registry=boot.legacy_registry,
        ontology=getattr(boot, "ontology", None),
        tenant_id=boot.tenant_id,
        kernel=getattr(boot, "_kernel", None),
        credential_store=getattr(boot, "credentials", None),
        environment_manager=getattr(boot, "_environment_manager", None),
        env_def_store=getattr(boot, "_env_def_store", None),
        env_service=getattr(boot, "_env_service", None),
        mcp_manager=getattr(boot, "_mcp_manager", None),
        tool_executor=getattr(boot, "_tool_executor", None),
        stripe_billing=getattr(boot, "_stripe_billing", None),
    )
    # Boot-completion flag for the /api/readiness probe (was _boot_complete kwarg).
    ctx.extras["boot_complete"] = bool(getattr(boot, "_running", False))
    set_context(ctx)
    return ctx
