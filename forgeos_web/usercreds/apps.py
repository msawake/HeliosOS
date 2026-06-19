from django.apps import AppConfig


class UserCredsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    # Module renamed from "secrets" to avoid shadowing Python's stdlib `secrets`
    # (used by Django password hashing) when manage.py runs as a script.
    name = "forgeos_web.usercreds"
    label = "forgeos_secrets"
