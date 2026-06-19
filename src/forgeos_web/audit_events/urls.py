"""URL routing for the audit / events / skills / workflows app.

Paths are byte-identical to the FastAPI routes in src/dashboard/fastapi_app.py
(no trailing slash; mounted at "" so the full path matches exactly).

Path-param mapping (per conventions):
- {workflow_id} -> <str:workflow_id>
- {name}        -> <str:name>   (FastAPI ``name: str``, not ``{name:path}``)

The literal /api/skills/domains and /api/skills/search must be declared before
/api/skills/<str:name> so they aren't swallowed by the detail route.
"""

from django.urls import path

from .views import (
    AuditView,
    EventsView,
    PlatformEventsView,
    SkillDetailView,
    SkillDomainsView,
    SkillSearchView,
    WorkflowDetailView,
    WorkflowsView,
)

urlpatterns = [
    # Audit
    path("api/audit", AuditView.as_view()),
    # Events
    path("api/events", EventsView.as_view()),
    path("api/platform/events", PlatformEventsView.as_view()),
    # Skills (literals before the detail catch)
    path("api/skills/domains", SkillDomainsView.as_view()),
    path("api/skills/search", SkillSearchView.as_view()),
    path("api/skills/<str:name>", SkillDetailView.as_view()),
    # Workflows
    path("api/workflows", WorkflowsView.as_view()),
    path("api/workflows/<str:workflow_id>", WorkflowDetailView.as_view()),
]
