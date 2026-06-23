"""Acting-user / caller helpers (identity labels, not authz identity).

Mirror the FastAPI ``current_user`` dependency (fastapi_app.py:528-536) and the
``x-forgeos-caller`` audit attribution header. Default ``"default"`` so legacy /
unauthenticated callers keep working.
"""

from __future__ import annotations


def acting_user(request) -> str:
    return request.headers.get("X-Forgeos-User") or "default"


def acting_caller(request) -> str:
    return request.headers.get("X-Forgeos-Caller") or "api"


def acting_principal(request, ctx, *, admin_role: str = "admin") -> tuple[str, str]:
    """Return ``(user_id, role)`` for the request — the authz identity.

    Auth disabled → caller is treated as admin so local tooling works unchanged.
    Auth enabled → identity comes from the DRF principal (``request.auth``, a
    ``forgeos_web.authn.principal.Principal``), with an ``X-Forgeos-User`` header
    override for the id. Used for per-resource (agent/secret/namespace)
    authorization. Mirrors fastapi_app.py:4307.
    """
    principal = getattr(request, "auth", None)
    uid = (
        request.headers.get("X-Forgeos-User")
        or (getattr(principal, "user_id", None) if principal else None)
        or "default"
    )
    if principal is not None:
        return uid, getattr(principal, "role", "viewer")
    return uid, ("viewer" if getattr(ctx, "auth_enabled", False) else admin_role)
