from django.apps import AppConfig


class AuthAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "src.forgeos_web.auth_app"
    label = "forgeos_auth"
