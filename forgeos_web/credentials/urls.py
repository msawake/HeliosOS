"""URL patterns for the credentials app.

Paths are byte-identical to the FastAPI routes (no trailing slashes); mounted
under "" so the full path equals the FastAPI path exactly.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("api/credentials/github", views.CredentialGithubView.as_view()),
    # `/api/credentials/jira` removed — Jira credentials are stored as plain
    # env vars on a per-user MCP via the generic MCP registration flow.
    path("api/credentials/secret", views.CredentialSecretView.as_view()),
    path("api/secrets", views.SecretsView.as_view()),
]
