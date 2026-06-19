from django.apps import AppConfig


class EventbusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "src.forgeos_web.eventbus"
    label = "forgeos_eventbus"
