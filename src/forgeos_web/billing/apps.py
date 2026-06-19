from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "src.forgeos_web.billing"
    label = "forgeos_billing"
