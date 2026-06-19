"""Django admin = the RBAC management surface (the primary "easy RBAC" goal).

Editing a user's ``role`` in this list writes the role string back to
``tenant_users`` (the column tokens read), and the post_save signal mirrors it
into the shadow Django User's Group membership. Capability grants per role are
managed through the standard Group admin (the 3 seeded Groups).
"""

from __future__ import annotations

from django.contrib import admin

from src.forgeos_web.tenancy.models import TenantUser

ROLE_CHOICES = (("admin", "admin"), ("operator", "operator"), ("viewer", "viewer"))


@admin.register(TenantUser)
class TenantUserAdmin(admin.ModelAdmin):
    list_display = ("email", "role", "tenant_id", "name")
    list_filter = ("role", "tenant_id")
    search_fields = ("email", "name", "tenant_id")
    list_editable = ("role",)  # inline role changes — the management surface
    ordering = ("email",)

    def has_add_permission(self, request):
        # Creation goes through /api/users (hashes passwords); admin only edits.
        return False
