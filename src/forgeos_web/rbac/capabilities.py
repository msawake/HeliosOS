"""Capability model for RBAC.

The 3 roles (admin/operator/viewer) map to Django Groups of the same name, each
granting capability Permissions. The Groups are the *editable surface* in Django
admin — granting a capability to a Group changes authorization with zero code.

``role_has`` resolves a capability for a role by querying the Group's permissions
(so admin edits take effect immediately) and falls back to the static ROLE_CAPS
map when the DB/Groups are unavailable (pre-migrate, tests). This keeps the
static defaults and the DB-backed surface in sync by construction (the seed
migration writes ROLE_CAPS into the Groups).
"""

from __future__ import annotations

# codename (without the "cap_" prefix) -> human description
CAPABILITIES: dict[str, str] = {
    "view": "View dashboards and resources",
    "approve": "Approve or reject HITL requests",
    "configure": "Create and configure agents",
    "manage_users": "Manage users and roles",
    "delete_agent": "Delete agents",
}

# Default role -> capability assignment (seeded into Groups by 0001_seed_rbac).
ROLE_CAPS: dict[str, set[str]] = {
    "viewer": {"view"},
    "operator": {"view", "approve"},
    "admin": set(CAPABILITIES),  # all
}

PERM_PREFIX = "cap_"


def role_has(role: str, capability: str) -> bool:
    """True if ``role`` grants ``capability``. DB Groups first, static fallback."""
    try:
        from django.contrib.auth.models import Group

        group = Group.objects.get(name=role)
        return group.permissions.filter(codename=f"{PERM_PREFIX}{capability}").exists()
    except Exception:
        return capability in ROLE_CAPS.get(role, set())
