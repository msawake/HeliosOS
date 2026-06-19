"""Process-global AuthManager + UserStore, built from the di AppContext.

Lazily constructed on first use (after boot has installed the context) and
cached. AuthManager binds to the boot tenant + db_client exactly as the FastAPI
factory did (fastapi_app.py:458).
"""

from __future__ import annotations

import threading

from forgeos_web import di

_auth_manager = None
_user_store = None
_lock = threading.Lock()


def get_auth_manager():
    """Return the shared AuthManager (or None if auth is disabled / no context)."""
    global _auth_manager
    if _auth_manager is not None:
        return _auth_manager
    with _lock:
        if _auth_manager is None:
            from src.api.auth import AuthManager

            ctx = di.try_get_context()
            db = ctx.db_client if ctx else None
            tenant = ctx.tenant_id if ctx else "default"
            _auth_manager = AuthManager(db_client=db, tenant_id=tenant)
    return _auth_manager


def get_user_store():
    """Return the shared UserStore for /api/users management."""
    global _user_store
    if _user_store is not None:
        return _user_store
    with _lock:
        if _user_store is None:
            from src.platform.user_store import UserStore

            ctx = di.try_get_context()
            db = ctx.db_client if ctx else None
            tenant = ctx.tenant_id if ctx else "default"
            _user_store = UserStore(db_client=db, tenant_id=tenant)
    return _user_store


def boot_tenant() -> str:
    ctx = di.try_get_context()
    return ctx.tenant_id if ctx else "default"
