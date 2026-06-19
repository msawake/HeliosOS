"""Seed the RBAC roles: 3 Groups (admin/operator/viewer) + capability Permissions.

Idempotent (get_or_create) so it is safe to re-run / fake. Capability
Permissions hang off a synthetic ContentType (app_label="forgeos_rbac",
model="capability") so they are independent of the managed=False domain models.
"""

from __future__ import annotations

from django.db import migrations

# Kept in sync with rbac/capabilities.py (imported lazily to stay migration-safe).
CAPABILITIES = {
    "view": "View dashboards and resources",
    "approve": "Approve or reject HITL requests",
    "configure": "Create and configure agents",
    "manage_users": "Manage users and roles",
    "delete_agent": "Delete agents",
}
ROLE_CAPS = {
    "viewer": {"view"},
    "operator": {"view", "approve"},
    "admin": set(CAPABILITIES),
}
PERM_PREFIX = "cap_"


def seed(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")

    ct, _ = ContentType.objects.get_or_create(app_label="forgeos_rbac", model="capability")

    perms = {}
    for codename, label in CAPABILITIES.items():
        perm, _ = Permission.objects.get_or_create(
            codename=f"{PERM_PREFIX}{codename}", content_type=ct,
            defaults={"name": label},
        )
        perms[codename] = perm

    for role, caps in ROLE_CAPS.items():
        group, _ = Group.objects.get_or_create(name=role)
        group.permissions.set([perms[c] for c in caps])


def unseed(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    Group.objects.filter(name__in=ROLE_CAPS).delete()
    ct = ContentType.objects.filter(app_label="forgeos_rbac", model="capability").first()
    if ct:
        Permission.objects.filter(content_type=ct, codename__startswith=PERM_PREFIX).delete()
        ct.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "__first__"),
        ("contenttypes", "__first__"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
