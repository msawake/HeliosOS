from django.apps import AppConfig


class RbacConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "src.forgeos_web.rbac"
    label = "forgeos_rbac"

    def ready(self):
        # Connect the TenantUser -> shadow-User / Group sync signals.
        from . import signals  # noqa: F401
