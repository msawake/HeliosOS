from django.urls import path

from .views import BillingUsageView

urlpatterns = [
    path("api/billing/usage", BillingUsageView.as_view()),
]
