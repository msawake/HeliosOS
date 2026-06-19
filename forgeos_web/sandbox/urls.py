from django.urls import path

from .views import SandboxRegisterView, SandboxResultView, SandboxToolView

urlpatterns = [
    path("api/sandbox/register", SandboxRegisterView.as_view()),
    path("api/sandbox/result", SandboxResultView.as_view()),
    path("api/sandbox/tool", SandboxToolView.as_view()),
]
