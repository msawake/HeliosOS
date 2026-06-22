"""Django admin for TENANT-scoped secrets (the platform/tenant management
surface). Writes route through ``CredentialStore.put_scoped_secret`` so values
are Fernet-encrypted and scope-qualified exactly like the API path — the raw
``enc_value`` bytea is never edited directly.

Only ``scope='tenant'`` rows are shown/managed here. User- and namespace-scoped
secrets are managed via the dashboard / ``/api/secrets`` (with their own RBAC).
This admin is reached only by Django staff/superusers (the admin-site gate).
"""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages

from forgeos_web import di
from forgeos_web.rbac.admin import AllTenantsMixin
from forgeos_web.usercreds.models import UserCredential
from src.platform.credentials import SCOPE_TENANT, logical_secret_name


def _tenant_store(tenant_id: str):
    """A CredentialStore bound to ``tenant_id`` (same encrypted backend the
    platform uses). ``None`` if no credential store is wired."""
    ctx = di.try_get_context() or di.AppContext()
    cs = getattr(ctx, "credential_store", None)
    if cs is None:
        return None
    from src.platform.credentials import CredentialStore

    return CredentialStore(cs._secrets, tenant_id=tenant_id or getattr(ctx, "tenant_id", "default") or "default")


class TenantSecretForm(forms.ModelForm):
    name = forms.CharField(
        label="Logical name",
        help_text="Stored as forgeos-tenant-<name>; referenced as secret:<name>.",
        required=False,
    )
    value = forms.CharField(
        label="Value",
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Encrypted on save. Required when creating a secret.",
    )

    class Meta:
        model = UserCredential
        fields = ("tenant_id", "kind")


class TenantSecretAdmin(AllTenantsMixin, admin.ModelAdmin):
    form = TenantSecretForm
    list_display = ("secret_name", "kind", "tenant_id", "scope", "updated_at")
    search_fields = ("secret_name", "tenant_id")
    ordering = ("secret_name",)

    def get_queryset(self, request):
        # Only tenant-scoped rows; user/namespace secrets are managed elsewhere.
        return super().get_queryset(request).filter(scope=SCOPE_TENANT)

    def has_change_permission(self, request, obj=None):
        # Disable in-place edit (re-create to rotate a value) — avoids ambiguity
        # between the logical name and the scope-qualified stored name.
        return False

    def save_model(self, request, obj, form, change):
        name = (form.cleaned_data.get("name") or "").strip()
        value = form.cleaned_data.get("value")
        if not name or not value:
            messages.error(request, "Both a logical name and a value are required.")
            return
        store = _tenant_store(obj.tenant_id)
        if store is None:
            messages.error(request, "Credential store not configured; nothing saved.")
            return
        try:
            store.put_scoped_secret(
                name, value, scope=SCOPE_TENANT,
                kind=(form.cleaned_data.get("kind") or "generic"), caller="django-admin",
            )
        except ValueError as e:
            messages.error(request, f"Could not store secret: {e}")
            return
        messages.success(request, f"Stored tenant secret '{name}'.")

    def delete_model(self, request, obj):
        store = _tenant_store(obj.tenant_id)
        if store is not None:
            name = logical_secret_name(obj.secret_name, scope=SCOPE_TENANT)
            store.delete_scoped_secret(name, scope=SCOPE_TENANT, caller="django-admin")


# rbac.admin._autoregister() may have already registered this model view-only
# (app-load order). Replace it with the writable tenant-secret admin.
try:
    admin.site.unregister(UserCredential)
except admin.sites.NotRegistered:
    pass
admin.site.register(UserCredential, TenantSecretAdmin)
