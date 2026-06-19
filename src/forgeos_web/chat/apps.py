from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "src.forgeos_web.chat"
    label = "forgeos_chat"
