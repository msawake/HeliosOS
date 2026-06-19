"""Django admin = the RBAC management surface (the primary "easy RBAC" goal).

Editing a user's ``role`` in this list writes the role string back to
``tenant_users`` (the column tokens read), and the post_save signal mirrors it
into the shadow Django User's Group membership. Capability grants per role are
managed through the standard Group admin (the 3 seeded Groups).
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered

from forgeos_web.agents.models import AgentRun, PlatformAgent
from forgeos_web.tenancy.models import Tenant, TenantUser

ROLE_CHOICES = (("admin", "admin"), ("operator", "operator"), ("viewer", "viewer"))


class AllTenantsMixin:
    """Admin sees every tenant's rows. TenantModel's default manager scopes to
    the request's tenant (contextvar), which for a platform-admin session would
    hide other tenants — so use the unscoped ``all_objects`` manager when present
    (RLS at the DB still applies for non-superuser DB roles)."""

    def get_queryset(self, request):
        mgr = getattr(self.model, "all_objects", None) or self.model._default_manager
        return mgr.all()


@admin.register(Tenant)
class TenantAdmin(AllTenantsMixin, admin.ModelAdmin):
    list_display = ("id", "name", "plan", "status")
    list_filter = ("plan", "status")
    search_fields = ("id", "name")

    def has_add_permission(self, request):
        return False


@admin.register(PlatformAgent)
class PlatformAgentAdmin(AllTenantsMixin, admin.ModelAdmin):
    list_display = ("agent_id", "name", "stack", "execution_type", "status", "tenant_id")
    list_filter = ("stack", "execution_type", "status", "tenant_id")
    search_fields = ("agent_id", "name")

    def has_add_permission(self, request):
        return False


@admin.register(AgentRun)
class AgentRunAdmin(AllTenantsMixin, admin.ModelAdmin):
    list_display = ("id", "agent_id", "status", "tenant_id")
    list_filter = ("status", "tenant_id")
    search_fields = ("id", "agent_id")

    def has_add_permission(self, request):
        return False


@admin.register(TenantUser)
class TenantUserAdmin(AllTenantsMixin, admin.ModelAdmin):
    list_display = ("email", "role", "tenant_id", "name")
    list_filter = ("role", "tenant_id")
    search_fields = ("email", "name", "tenant_id")
    list_editable = ("role",)  # inline role changes — the management surface
    ordering = ("email",)

    def has_add_permission(self, request):
        # Creation goes through /api/users (hashes passwords); admin only edits.
        return False


# --------------------------------------------------------------------------- #
# Auto-register every remaining ForgeOS table so all data is visible in admin.
# View-only (rows not clickable) so managed=False quirks — VectorField change
# forms, composite-PK change views — can't error; the list view is the goal.
# --------------------------------------------------------------------------- #
class AutoModelAdmin(AllTenantsMixin, admin.ModelAdmin):
    list_per_page = 50
    list_display_links = None  # browse-only; no change-view navigation

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def _autoregister() -> None:
    for model in django_apps.get_models():
        if not model._meta.app_label.startswith("forgeos_"):
            continue
        # Django admin cannot register composite-PK models (EventSubscription,
        # ScheduledJob, AgentEnvironment, EnvironmentDef, Namespace,
        # NamespacePolicy, NamespaceAdmin). Skip them — surfaced via the API.
        if type(model._meta.pk).__name__ == "CompositePrimaryKey":
            continue
        try:
            admin.site.register(model, AutoModelAdmin)
        except AlreadyRegistered:
            pass  # explicit admins above win


_autoregister()
