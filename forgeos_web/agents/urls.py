"""URL routing for the platform agents app.

Paths are byte-identical to the FastAPI routes in src/dashboard/fastapi_app.py
(no trailing slash; mounted at "" so the full path matches exactly). Order
matters: literal segments (from-yaml) are declared before the {agent_id} catch
in FastAPI by virtue of distinct paths — Django dispatches on full-path match so
order is not load-bearing here, but the more specific sub-resources are listed
under the detail route for readability.
"""

from django.urls import path

from .views import (
    AgentDetailView,
    AgentEnvironmentView,
    AgentFromYamlUpdateView,
    AgentHeartbeatView,
    AgentInvokeView,
    AgentLogsView,
    AgentRunsView,
    AgentShellView,
    AgentStopView,
    AgentsFromYamlView,
    AgentsView,
    AuditRecentView,
    BudgetsView,
    FleetView,
    OverviewView,
    ProcessDetailView,
    PsView,
    RunDetailView,
    SchedulerView,
    SignalsView,
    TeamDetailView,
    TeamsView,
    ToolsView,
)

urlpatterns = [
    # Collection + creation
    path("api/platform/agents", AgentsView.as_view()),
    path("api/platform/agents/from-yaml", AgentsFromYamlView.as_view()),
    # Agent detail + sub-resources
    path("api/platform/agents/<str:agent_id>", AgentDetailView.as_view()),
    path("api/platform/agents/<str:agent_id>/from-yaml", AgentFromYamlUpdateView.as_view()),
    path("api/platform/agents/<str:agent_id>/invoke", AgentInvokeView.as_view()),
    path("api/platform/agents/<str:agent_id>/stop", AgentStopView.as_view()),
    path("api/platform/agents/<str:agent_id>/shell", AgentShellView.as_view()),
    path("api/platform/agents/<str:agent_id>/heartbeat", AgentHeartbeatView.as_view()),
    path("api/platform/agents/<str:agent_id>/runs", AgentRunsView.as_view()),
    path("api/platform/agents/<str:agent_id>/environment", AgentEnvironmentView.as_view()),
    # Runs / process table / signals
    path("api/platform/runs/<str:run_id>", RunDetailView.as_view()),
    path("api/platform/ps", PsView.as_view()),
    path("api/platform/process/<str:pid>", ProcessDetailView.as_view()),
    path("api/platform/signals/<str:pid>", SignalsView.as_view()),
    # Teams
    path("api/platform/teams", TeamsView.as_view()),
    path("api/platform/teams/<str:namespace>/<str:name>", TeamDetailView.as_view()),
    # Misc reads
    path("api/platform/tools", ToolsView.as_view()),
    path("api/platform/agent-logs", AgentLogsView.as_view()),
    path("api/platform/overview", OverviewView.as_view()),
    path("api/platform/budgets", BudgetsView.as_view()),
    path("api/platform/fleet", FleetView.as_view()),
    path("api/platform/scheduler", SchedulerView.as_view()),
    path("api/platform/audit/recent", AuditRecentView.as_view()),
]
