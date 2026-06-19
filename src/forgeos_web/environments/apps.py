from django.apps import AppConfig


class EnvironmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "src.forgeos_web.environments"
    label = "forgeos_environments"
