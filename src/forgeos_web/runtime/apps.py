from django.apps import AppConfig


class RuntimeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "src.forgeos_web.runtime"
    label = "forgeos_runtime"
