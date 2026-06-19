"""DRF permission classes reproducing the FastAPI auth gate + role checks.

- ``IsAuthenticatedOrPublicPath`` is the global default permission: it mirrors
  ``check_auth`` (fastapi_app.py:469-508) — public paths and public GET prefixes
  are open; otherwise a valid principal is required.
- ``require_role(*roles)`` builds a permission class mirroring the FastAPI
  ``require_role`` dependency (fastapi_app.py:510-526): 401 if unauthenticated,
  403 if the role is not allowed.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework.permissions import BasePermission

# Verbatim from fastapi_app.py:442-451.
PUBLIC_PATHS = {
    "/api/health", "/api/readiness", "/api/liveness", "/", "/admin", "/intelligence",
    "/docs", "/redoc", "/openapi.json",
    "/api/auth/token", "/api/auth/login", "/api/me",
}
PUBLIC_READ_PREFIXES = ("/api/approvals",)  # GET only


def _auth_enabled() -> bool:
    return getattr(settings, "FORGEOS_AUTH_ENABLED", True)


class IsAuthenticatedOrPublicPath(BasePermission):
    def has_permission(self, request, view):
        if not _auth_enabled():
            return True
        path = request.path
        if path in PUBLIC_PATHS:
            return True
        if request.method == "GET" and any(path.startswith(p) for p in PUBLIC_READ_PREFIXES):
            return True
        return getattr(request, "auth", None) is not None


class RequireRole(BasePermission):
    required_roles: tuple[str, ...] = ()

    def has_permission(self, request, view):
        if not _auth_enabled():
            return True
        principal = getattr(request, "auth", None)
        if principal is None:
            return False  # -> 401 (no successful authenticator)
        if principal.role not in self.required_roles:
            self.message = (
                f"Role '{principal.role}' not authorized "
                f"(requires {', '.join(self.required_roles)})"
            )
            return False  # -> 403
        return True


def require_role(*roles: str):
    """Return a permission class requiring one of ``roles`` (FastAPI parity)."""
    name = "RequireRole_" + "_".join(roles)
    return type(name, (RequireRole,), {"required_roles": tuple(roles)})


class HasCapability(BasePermission):
    """Capability-based gate (extensible RBAC). Resolves the principal's role
    against the capability Groups managed in Django admin — so granting a
    capability to a role in admin changes access with no code change."""

    capability: str | None = None

    def has_permission(self, request, view):
        if not _auth_enabled():
            return True
        principal = getattr(request, "auth", None)
        if principal is None:
            return False
        from src.forgeos_web.rbac.capabilities import role_has

        if role_has(principal.role, self.capability):
            return True
        self.message = f"Capability '{self.capability}' required"
        return False


def has_capability(capability: str):
    """Return a permission class requiring ``capability`` (e.g. 'delete_agent')."""
    return type(f"HasCapability_{capability}", (HasCapability,), {"capability": capability})
