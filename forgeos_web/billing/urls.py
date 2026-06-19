from django.urls import path

from .views import (
    BillingMeteringView,
    BillingUsageByCompanyView,
    BillingUsageView,
)

urlpatterns = [
    path("api/billing/metering", BillingMeteringView.as_view()),
    path("api/billing/usage", BillingUsageView.as_view()),
    path("api/billing/usage/<str:company_id>", BillingUsageByCompanyView.as_view()),
]
