"""Approvals / HITL / A2H URL configuration.

Paths preserved byte-for-byte from src/dashboard/fastapi_app.py (no trailing
slash). Mounted under "" at the project root so the full path equals the
FastAPI path exactly.
"""

from django.urls import path

from .views import (
    A2HApproveView,
    A2HChatCloseView,
    A2HChatDetailView,
    A2HChatMessagesView,
    A2HChatsView,
    A2HHumansView,
    A2HNotificationsView,
    A2HPendingView,
    A2HRejectView,
    A2HRequestDetailView,
    A2HRequestsView,
    A2HRespondView,
    ApprovalApproveView,
    ApprovalDetailView,
    ApprovalRejectView,
    ApprovalsView,
    DebugA2HView,
    HitlPendingView,
)

urlpatterns = [
    # Approvals
    path("api/approvals", ApprovalsView.as_view()),
    path("api/approvals/<str:request_id>", ApprovalDetailView.as_view()),
    path("api/approvals/<str:request_id>/approve", ApprovalApproveView.as_view()),
    path("api/approvals/<str:request_id>/reject", ApprovalRejectView.as_view()),
    # HITL / debug
    path("api/hitl/pending", HitlPendingView.as_view()),
    path("api/_debug/a2h", DebugA2HView.as_view()),
    # A2H protocol
    path("api/a2h/humans", A2HHumansView.as_view()),
    path("api/a2h/notifications", A2HNotificationsView.as_view()),
    path("api/a2h/pending", A2HPendingView.as_view()),
    path("api/a2h/requests", A2HRequestsView.as_view()),
    path("api/a2h/requests/<str:request_id>", A2HRequestDetailView.as_view()),
    path("api/a2h/requests/<str:request_id>/approve", A2HApproveView.as_view()),
    path("api/a2h/requests/<str:request_id>/reject", A2HRejectView.as_view()),
    path("api/a2h/requests/<str:request_id>/respond", A2HRespondView.as_view()),
    # A2H chat
    path("api/a2h/v1/chats", A2HChatsView.as_view()),
    path("api/a2h/v1/chats/<str:chat_id>", A2HChatDetailView.as_view()),
    path("api/a2h/v1/chats/<str:chat_id>/close", A2HChatCloseView.as_view()),
    path("api/a2h/v1/chats/<str:chat_id>/messages", A2HChatMessagesView.as_view()),
]
