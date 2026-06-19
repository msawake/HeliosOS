from django.apps import AppConfig


class EnvironmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "forgeos_web.environments"
    label = "forgeos_environments"
