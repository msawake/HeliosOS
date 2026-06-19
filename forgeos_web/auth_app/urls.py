from django.urls import path

from .views import DevTokenView, LoginView, MeView, UserDetailView, UsersView

urlpatterns = [
    path("api/auth/token", DevTokenView.as_view()),
    path("api/auth/login", LoginView.as_view()),
    path("api/me", MeView.as_view()),
    path("api/users", UsersView.as_view()),
    path("api/users/<str:user_id>", UserDetailView.as_view()),
]
