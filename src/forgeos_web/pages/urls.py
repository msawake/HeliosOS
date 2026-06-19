from django.urls import path

from .views import (
    AdminPageView,
    DashboardPageView,
    IntelligencePageView,
    MetricsView,
)

urlpatterns = [
    path("", DashboardPageView.as_view()),
    path("admin", AdminPageView.as_view()),
    path("intelligence", IntelligencePageView.as_view()),
    path("metrics", MetricsView.as_view()),
]
