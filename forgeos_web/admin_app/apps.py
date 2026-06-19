from django.apps import AppConfig


class AdminAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "forgeos_web.admin_app"
    label = "forgeos_admin"
