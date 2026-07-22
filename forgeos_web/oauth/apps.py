from django.apps import AppConfig


class OAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "forgeos_web.oauth"
    label = "forgeos_oauth"
