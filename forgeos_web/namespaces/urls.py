from django.urls import path

from .views import (
    NamespaceAdminDetailView,
    NamespaceAdminsView,
    NamespaceDetailView,
    NamespacesView,
)

urlpatterns = [
    path("api/platform/namespaces", NamespacesView.as_view()),
    path("api/platform/namespaces/<str:ns>", NamespaceDetailView.as_view()),
    path("api/platform/namespaces/<str:ns>/admins", NamespaceAdminsView.as_view()),
    path("api/platform/namespaces/<str:ns>/admins/<str:admin_user_id>",
         NamespaceAdminDetailView.as_view()),
]
