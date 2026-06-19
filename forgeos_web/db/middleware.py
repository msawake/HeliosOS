"""Per-request RLS binding.

Sets the tenant contextvar + ``app.current_tenant`` for the lifetime of the
request. With ``ATOMIC_REQUESTS=True`` the request runs in one transaction, so
the ``set_config(local)`` issued here is live for every ORM query and clears at
commit.

Tenant resolution prefers the authenticated principal (DRF auth attaches it in
Step 3) and falls back to an explicit header, then "default". SSE / streaming
views are exempt from ATOMIC_REQUESTS and must instead wrap DB work in
``tenant_context(...)`` directly.
"""

from __future__ import annotations

from .rls import bind_var, reset_var, reset_tenant, set_tenant


def resolve_tenant(request) -> str:
    auth = getattr(request, "auth", None)
    user = getattr(request, "user", None)
    tid = (
        getattr(auth, "tenant_id", None)
        or getattr(user, "tenant_id", None)
        or request.META.get("HTTP_X_FORGEOS_TENANT")
    )
    return tid or "default"


class RLSMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_id = resolve_tenant(request)
        token = bind_var(tenant_id)
        set_tenant(tenant_id)
        try:
            return self.get_response(request)
        finally:
            reset_tenant()
            reset_var(token)
