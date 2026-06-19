"""DRF authentication that mirrors the FastAPI ``check_auth`` credential paths.

Subsumes both the Bearer-token and X-API-Key paths because
``AuthManager.authenticate`` already tries them in the same order the FastAPI app
relied on (signed session token -> Firebase JWT; admin key -> per-tenant key,
see auth.py:357-388). Returns ``None`` (anonymous) when no credential is present
or valid — the 401/403 decision is made by the permission layer, exactly as
``check_auth`` deferred it for public paths.

On success it also binds the principal's tenant for RLS. Authentication runs
inside the view's atomic transaction (ATOMIC_REQUESTS), so the
``set_config(local)`` issued here is live for every ORM query in the request.
"""

from __future__ import annotations

from rest_framework.authentication import BaseAuthentication

from forgeos_web.db import rls

from .manager import get_auth_manager
from .permissions import _auth_enabled
from .principal import Principal
from .shim import DjangoAuthRequest


class ForgeOSAuthentication(BaseAuthentication):
    def authenticate(self, request):
        if not _auth_enabled():
            return None

        manager = get_auth_manager()
        user = manager.authenticate(DjangoAuthRequest(request)) if manager else None

        if user is None:
            # Dev escape hatch — only when explicitly enabled (auth.py:497-506).
            import os

            if os.environ.get("FORGEOS_ALLOW_DEV_LOGIN", "").lower() in ("1", "true", "yes"):
                header = request.headers.get("Authorization", "")
                if header.startswith("Bearer ") and header[7:].startswith("dev-"):
                    from src.api.auth import AuthUser, UserRole
                    from .manager import boot_tenant

                    user = AuthUser(
                        user_id="dev", email="dev@local", tenant_id=boot_tenant(),
                        role=UserRole.ADMIN, name="dev",
                    )

        if user is None:
            return None  # anonymous; permission layer decides

        # Bind tenant for RLS (contextvar for the manager + DB session var).
        rls.bind_var(user.tenant_id)
        rls.set_tenant(user.tenant_id)
        return (Principal(user), user)

    def authenticate_header(self, request):
        # Drives DRF to return 401 (not 403) when authentication is required.
        return 'Bearer realm="forgeos"'
