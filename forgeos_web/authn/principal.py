"""DRF ``request.user`` adapter around an AuthUser.

DRF needs a user object exposing ``is_authenticated``. We wrap AuthUser so the
role/tenant_id/email are first-class and the capability helpers (can_approve …)
remain available via attribute fallthrough.
"""

from __future__ import annotations


class Principal:
    is_authenticated = True
    is_anonymous = False

    def __init__(self, auth_user):
        self._u = auth_user
        self.user_id = auth_user.user_id
        self.email = auth_user.email
        self.tenant_id = auth_user.tenant_id
        self.role = auth_user.role
        self.name = auth_user.name

    def __getattr__(self, item):
        # Fall through to AuthUser for can_approve()/can_configure()/to_dict().
        return getattr(self._u, item)

    def __str__(self):
        return f"{self.email} ({self.role}@{self.tenant_id})"
