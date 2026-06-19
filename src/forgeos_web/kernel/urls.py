"""URL patterns for the kernel app.

Paths are byte-identical to the FastAPI routes (no trailing slashes); mounted
under "" so the full path equals the FastAPI path exactly. FastAPI's
``{agent_id}`` / ``{namespace}`` / ``{job_id}`` become ``<str:...>``.

Ordering note: the literal ``namespace-policies`` is registered before the
``namespace-policy/<str:namespace>`` pattern so it is matched first (the policy
list endpoint is a distinct path, not a namespace named "policies", but keeping
literals first is the safe convention).
"""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    # Kernel policy decisions.
    path("api/platform/kernel/check-tool", views.KernelCheckToolView.as_view()),
    path("api/platform/kernel/check-a2a", views.KernelCheckA2AView.as_view()),
    path("api/platform/kernel/check-data", views.KernelCheckDataView.as_view()),
    path("api/platform/kernel/contract/<str:agent_id>", views.KernelContractView.as_view()),
    path("api/platform/kernel/admit", views.KernelAdmitView.as_view()),
    path("api/platform/kernel/effective-policy/<str:agent_id>",
         views.KernelEffectivePolicyView.as_view()),
    path("api/platform/kernel/check-license", views.KernelCheckLicenseView.as_view()),
    path("api/platform/kernel/audit", views.KernelAuditView.as_view()),
    # Durable policy management.
    path("api/platform/kernel/namespace-policies", views.NamespacePoliciesView.as_view()),
    path("api/platform/kernel/namespace-policy/<str:namespace>",
         views.NamespacePolicyDetailView.as_view()),
    path("api/platform/kernel/global-policy", views.GlobalPolicyView.as_view()),
    # Remote agent governance + async A2A task queue.
    path("api/platform/kernel/usage", views.UsageReportView.as_view()),
    path("api/platform/a2a/submit", views.A2ASubmitView.as_view()),
    path("api/platform/a2a/jobs/<str:job_id>", views.A2AJobView.as_view()),
    path("api/platform/a2a/result", views.A2AResultView.as_view()),
    path("api/platform/a2a/fail", views.A2AFailView.as_view()),
    path("api/platform/a2a/tasks/pending", views.A2APendingTasksView.as_view()),
    # Inter-agent messages.
    path("api/platform/messages", views.MessagesView.as_view()),
    path("api/platform/messages/<str:agent_id>", views.MessagesForAgentView.as_view()),
]
