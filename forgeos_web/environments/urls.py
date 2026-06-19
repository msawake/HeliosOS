"""URL routing for the platform environments app.

Paths are byte-identical to the FastAPI routes in src/dashboard/fastapi_app.py
(no trailing slash; mounted at "" so the full path matches exactly).
"""

from django.urls import path

from .views import EnvironmentDetailView, EnvironmentsView

urlpatterns = [
    path("api/platform/environments", EnvironmentsView.as_view()),
    path("api/platform/environments/<str:env_def_id>", EnvironmentDetailView.as_view()),
]
