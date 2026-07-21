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
    # Tool discovery for the agent-creation wizard — MUST precede the
    # <server_name> detail route so the ``/tools`` suffix isn't swallowed.
    path("api/platform/mcp/servers/<str:server_name>/tools",
         views.PlatformMcpServerToolsView.as_view()),
    path("api/platform/mcp/servers/<str:server_name>",
         views.PlatformMcpServerDetailView.as_view()),
    # `/mcp/jira` was a Jira-specific enroll shortcut. Removed — the generic
    # per-server endpoint below handles Jira (and every other MCP) uniformly.
    path("api/users/<str:user_id>/mcp/<str:server_name>", views.UserMcpView.as_view()),
    path("api/namespaces/<str:ns>/mcp/<str:server_name>", views.NamespaceMcpView.as_view()),
    # MCP access groups (migration 024) — Django-native (no FastAPI equivalent).
    path("api/mcp/access-groups", views.McpAccessGroupsView.as_view()),
    path("api/mcp/access-groups/<str:name>", views.McpAccessGroupDetailView.as_view()),
]
