"""URL patterns for the mcps app.

Paths are byte-identical to the FastAPI routes (no trailing slashes); mounted
under "" so the full path equals the FastAPI path exactly. FastAPI's
``{name:path}`` becomes ``<path:name>``.

NOTE: /api/mcps/search and /api/mcps/categories MUST precede the
``<path:name>`` catch-all, otherwise the path converter would swallow them.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("api/mcps/categories", views.McpCategoriesView.as_view()),
    path("api/mcps/search", views.McpSearchView.as_view()),
    path("api/mcps/<path:name>", views.McpPackageView.as_view()),
    path("api/platform/mcp/servers", views.PlatformMcpServersView.as_view()),
    path("api/platform/mcp/servers/<str:server_name>",
         views.PlatformMcpServerDetailView.as_view()),
    path("api/users/<str:user_id>/mcp/jira", views.UserJiraMcpView.as_view()),
    path("api/users/<str:user_id>/mcp/<str:server_name>", views.UserMcpView.as_view()),
    path("api/namespaces/<str:ns>/mcp/<str:server_name>", views.NamespaceMcpView.as_view()),
]
