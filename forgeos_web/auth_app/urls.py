from django.urls import path

from .views import (
    DevTokenView,
    LoginView,
    MeView,
    PersonalTokenDetailView,
    PersonalTokensView,
    UserDetailView,
    UsersView,
)

urlpatterns = [
    path("api/auth/token", DevTokenView.as_view()),
    path("api/auth/login", LoginView.as_view()),
    path("api/me", MeView.as_view()),
    path("api/users", UsersView.as_view()),
    path("api/users/<str:user_id>", UserDetailView.as_view()),
    # Personal Access Tokens — long-lived Bearer tokens the user mints from
    # the dashboard to configure MCP / CLI clients (see
    # src/api/personal_tokens.py).
    path("api/tokens", PersonalTokensView.as_view()),
    path("api/tokens/<str:token_id>", PersonalTokenDetailView.as_view()),
]
