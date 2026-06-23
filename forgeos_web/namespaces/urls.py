from django.urls import path

from .views import (
    MyNamespacesView,
    NamespaceAdminDetailView,
    NamespaceAdminsView,
    NamespaceDetailView,
    NamespaceMemberDetailView,
    NamespaceMembersView,
    NamespacesView,
)

urlpatterns = [
    path("api/platform/namespaces", NamespacesView.as_view()),
    # `mine` is a literal and must precede the `<str:ns>` catch-all below.
    path("api/platform/namespaces/mine", MyNamespacesView.as_view()),
    path("api/platform/namespaces/<str:ns>", NamespaceDetailView.as_view()),
    path("api/platform/namespaces/<str:ns>/admins", NamespaceAdminsView.as_view()),
    path("api/platform/namespaces/<str:ns>/admins/<str:admin_user_id>",
         NamespaceAdminDetailView.as_view()),
    path("api/platform/namespaces/<str:ns>/members", NamespaceMembersView.as_view()),
    path("api/platform/namespaces/<str:ns>/members/<str:member_user_id>",
         NamespaceMemberDetailView.as_view()),
]
