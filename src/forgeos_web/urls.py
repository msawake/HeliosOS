"""Root URL configuration.

Each ForgeOS web app owns a urls.py mounted here. Paths are preserved
byte-for-byte from the FastAPI app so the Next.js dashboard contract is
unchanged. Apps are added per workstream; Step 1 mounts health + admin.
"""

from __future__ import annotations

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("src.forgeos_web.health.urls")),
    path("", include("src.forgeos_web.auth_app.urls")),
    path("", include("src.forgeos_web.approvals.urls")),
    path("", include("src.forgeos_web.agents.urls")),
    path("", include("src.forgeos_web.mcps.urls")),
    path("", include("src.forgeos_web.clients.urls")),
    path("", include("src.forgeos_web.kernel.urls")),
    path("", include("src.forgeos_web.namespaces.urls")),
    path("", include("src.forgeos_web.credentials.urls")),
    path("", include("src.forgeos_web.environments.urls")),
    path("", include("src.forgeos_web.admin_app.urls")),
    path("", include("src.forgeos_web.intelligence.urls")),
    path("", include("src.forgeos_web.billing.urls")),
    path("", include("src.forgeos_web.audit_events.urls")),
    path("", include("src.forgeos_web.sandbox.urls")),
    path("", include("src.forgeos_web.pages.urls")),
    path("", include("src.forgeos_web.chat.urls")),
    # Remaining: /ws/agents (WebSocket — needs Django Channels; tracked separately).
]
