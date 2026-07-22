"""OAuth 2.0 authorization-server routes (Django-native; no FastAPI ancestor).

Mounted at "" so the full paths are exactly what MCP clients discover. The
``/oauth/*`` + ``/.well-known/*`` protocol endpoints are public (added to
authn.permissions.PUBLIC_PATHS); the consent-decision and grant-management
endpoints require a real user login.
"""

from django.urls import path

from .views import (
    AuthorizeDecisionView,
    AuthorizeView,
    AuthServerMetadataView,
    OAuthGrantDetailView,
    OAuthGrantsView,
    RegisterView,
    RevokeView,
    TokenView,
)

urlpatterns = [
    # Discovery + protocol (public)
    path(".well-known/oauth-authorization-server", AuthServerMetadataView.as_view()),
    path("oauth/register", RegisterView.as_view()),
    path("oauth/authorize", AuthorizeView.as_view()),
    path("oauth/token", TokenView.as_view()),
    path("oauth/revoke", RevokeView.as_view()),
    # Consent decision (authenticated — the dashboard SPA calls these)
    path("oauth/authorize/<str:request_id>", AuthorizeDecisionView.as_view()),
    # Grant management (authenticated dashboard API)
    path("api/oauth/grants", OAuthGrantsView.as_view()),
    path("api/oauth/grants/<str:client_id>", OAuthGrantDetailView.as_view()),
]
