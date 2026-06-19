from django.apps import AppConfig


class AuditEventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "forgeos_web.audit_events"
    label = "forgeos_audit"
