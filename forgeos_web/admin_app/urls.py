"""URL routing for the admin orchestrator app.

Paths are byte-identical to the FastAPI routes in src/dashboard/fastapi_app.py
(no trailing slash; mounted at "" so the full path matches exactly).

NOTE: POST /api/admin/chat/stream (SSE) is intentionally not routed here — it is
handled separately in the streaming step.
"""

from django.urls import path

from .views import (
    AdminChatView,
    AdminEventsView,
    AdminHealthView,
    AdminKnowledgeView,
    AdminMetricsView,
    AdminProvidersView,
)

urlpatterns = [
    path("api/admin/chat", AdminChatView.as_view()),
    path("api/admin/events", AdminEventsView.as_view()),
    path("api/admin/health", AdminHealthView.as_view()),
    path("api/admin/knowledge", AdminKnowledgeView.as_view()),
    path("api/admin/metrics", AdminMetricsView.as_view()),
    path("api/admin/providers", AdminProvidersView.as_view()),
]
