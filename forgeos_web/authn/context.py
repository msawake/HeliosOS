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
