from django.urls import path

from .views import (
    ClientAgentsView,
    ClientDetailView,
    ClientMcpServerDetailView,
    ClientMcpServersView,
    ClientsView,
)

urlpatterns = [
    path("api/clients", ClientsView.as_view()),
    path("api/clients/<str:client_id>", ClientDetailView.as_view()),
    path("api/clients/<str:client_id>/agents", ClientAgentsView.as_view()),
    path("api/clients/<str:client_id>/mcp-servers", ClientMcpServersView.as_view()),
    path("api/clients/<str:client_id>/mcp-servers/<str:server_name>",
         ClientMcpServerDetailView.as_view()),
]
