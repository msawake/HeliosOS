"""URL routing for the chat app.

Paths are byte-identical to the FastAPI routes in src/dashboard/fastapi_app.py
(no trailing slash; mounted at "" so the full path matches exactly). The SSE
endpoints are plain Django ``async def`` views (StreamingHttpResponse); the JSON
endpoints are DRF APIViews.

Mounting (follow-up): add `path("", include("src.forgeos_web.chat.urls"))` to
src/forgeos_web/urls.py and `"src.forgeos_web.chat"` to INSTALLED_APPS. Left
unmounted here per the porting task's "do not modify project settings/urls".
"""

from django.urls import path

from . import views

urlpatterns = [
    # SSE (plain async views)
    path("api/platform/agents/<str:agent_id>/chat/stream", views.agent_chat_stream),
    path("api/platform/agents/<str:agent_id>/chat/resume", views.agent_chat_resume),
    path("api/admin/chat/stream", views.admin_chat_stream),
    # JSON (DRF APIViews)
    path("api/platform/agents/<str:agent_id>/chat/sessions", views.ChatSessionsView.as_view()),
    path("api/platform/agents/<str:agent_id>/chat/history", views.ChatHistoryView.as_view()),
    path("api/platform/agents/<str:agent_id>/chat/sessions/<str:session_id>",
         views.ChatSessionDetailView.as_view()),
    path("api/platform/wizard/chat", views.WizardChatView.as_view()),
]
