from django.urls import path

from .views import HealthView, LivenessView, ReadinessView

urlpatterns = [
    path("api/health", HealthView.as_view()),
    path("api/readiness", ReadinessView.as_view()),
    path("api/liveness", LivenessView.as_view()),
]
