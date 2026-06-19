"""URL patterns for the credentials app.

Paths are byte-identical to the FastAPI routes (no trailing slashes); mounted
under "" so the full path equals the FastAPI path exactly.
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("api/credentials/github", views.CredentialGithubView.as_view()),
    path("api/credentials/jira", views.CredentialJiraView.as_view()),
    path("api/credentials/secret", views.CredentialSecretView.as_view()),
    path("api/secrets", views.SecretsView.as_view()),
]
