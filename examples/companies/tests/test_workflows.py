"""Tests for LeadForge AI workflow definitions and the workflow engine."""

import pytest
from src.workflows.definitions import (
    TaskGraphBuilder,
    TaskPriority,
    TaskStatus,
    WorkflowEngine,
    WorkflowStatus,
    create_client_onboarding_workflow,
    create_compliance_audit_workflow,
    create_lead_nurture_workflow,
    create_lead_qualification_workflow,
    create_leadforge_sales_workflow,
    create_marketing_campaign_workflow,
    create_outbound_campaign_workflow,
    create_abm_campaign_workflow,
    create_financial_reporting_workflow,
)


class TestTaskGraphBuilder:
    def test_build_simple_graph(self):
        wf = (
            TaskGraphBuilder("Test Workflow")
            .task("step1", "agent-a", "Do step 1")
            .task("step2", "agent-b", "Do step 2", blocked_by=["step1"])
            .task("step3", "agent-c", "Do step 3", blocked_by=["step2"])
            .build()
        )
        assert len(wf.tasks) == 3
        assert wf.name == "Test Workflow"

    def test_parallel_tasks(self):
        wf = (
            TaskGraphBuilder("Parallel")
            .task("a", "agent-1", "Task A")
            .task("b", "agent-2", "Task B")
            .task("c", "agent-3", "Task C")
            .task("join", "agent-4", "Join", blocked_by=["a", "b", "c"])
            .build()
        )
        ready = wf.get_ready_tasks()
        assert len(ready) == 3
        assert all(t.name in ("a", "b", "c") for t in ready)

    def test_blocked_by_creates_blocks(self):
        wf = (
            TaskGraphBuilder("Deps")
            .task("first", "agent-1")
            .task("second", "agent-2", blocked_by=["first"])
            .build()
        )
        first = [t for t in wf.tasks.values() if t.name == "first"][0]
        second = [t for t in wf.tasks.values() if t.name == "second"][0]
        assert second.task_id in first.blocks
        assert first.task_id in second.blocked_by


class TestWorkflowDefinition:
    def test_get_ready_tasks_respects_dependencies(self):
        wf = (
            TaskGraphBuilder("Test")
            .task("a", "agent-1")
            .task("b", "agent-2", blocked_by=["a"])
            .build()
        )
        ready = wf.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].name == "a"

    def test_ready_tasks_after_completion(self):
        wf = (
            TaskGraphBuilder("Test")
            .task("a", "agent-1")
            .task("b", "agent-2", blocked_by=["a"])
            .build()
        )
        task_a = [t for t in wf.tasks.values() if t.name == "a"][0]
        task_a.status = TaskStatus.COMPLETED
        ready = wf.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].name == "b"

    def test_is_complete(self):
        wf = (
            TaskGraphBuilder("Test")
            .task("a", "agent-1")
            .task("b", "agent-2")
            .build()
        )
        assert not wf.is_complete()
        for task in wf.tasks.values():
            task.status = TaskStatus.COMPLETED
        assert wf.is_complete()

    def test_progress_tracking(self):
        wf = (
            TaskGraphBuilder("Test")
            .task("a", "agent-1")
            .task("b", "agent-2")
            .task("c", "agent-3")
            .build()
        )
        progress = wf.get_progress()
        assert progress["total"] == 3
        assert progress["pending"] == 3
        assert progress["completed"] == 0


class TestWorkflowTemplates:
    def test_lead_qualification_workflow(self):
        wf = create_lead_qualification_workflow(
            "Sarah Chen", "sarah@techcorp.com", "TechCorp", "Acme SaaS"
        )
        assert len(wf.tasks) == 5
        assert wf.workflow_type == "operational"
        ready = wf.get_ready_tasks()
        assert any(t.name == "research" for t in ready)

    def test_lead_nurture_workflow(self):
        wf = create_lead_nurture_workflow(
            "John Doe", "john@example.com", "ExampleCo", "Acme SaaS"
        )
        assert len(wf.tasks) == 5
        assert wf.workflow_type == "operational"
        ready = wf.get_ready_tasks()
        assert any(t.name == "design_sequence" for t in ready)

    def test_outbound_campaign_workflow(self):
        wf = create_outbound_campaign_workflow(
            "Acme SaaS", "Q2 Outbound", "VP Sales at B2B SaaS",
            channels=["email", "linkedin"], daily_volume=50,
        )
        assert len(wf.tasks) == 8
        assert wf.workflow_type == "project"
        ready = wf.get_ready_tasks()
        assert any(t.name == "icp_definition" for t in ready)

    def test_abm_campaign_workflow(self):
        wf = create_abm_campaign_workflow(
            "Acme SaaS", ["TechCorp", "DataFlow", "CloudBase"]
        )
        assert len(wf.tasks) == 7
        assert wf.workflow_type == "project"

    def test_client_onboarding_workflow(self):
        wf = create_client_onboarding_workflow(
            "Acme SaaS", "cto@acme.com", 5000, ["email", "linkedin"]
        )
        assert len(wf.tasks) == 8
        assert wf.workflow_type == "operational"
        ready = wf.get_ready_tasks()
        assert any(t.name == "contract_review" for t in ready)

    def test_leadforge_sales_workflow(self):
        wf = create_leadforge_sales_workflow(
            "John Smith", "john@prospect.com", company="ProspectCo"
        )
        assert len(wf.tasks) == 7

    def test_financial_reporting_workflow(self):
        wf = create_financial_reporting_workflow("monthly")
        assert len(wf.tasks) == 4

    def test_marketing_campaign_workflow(self):
        wf = create_marketing_campaign_workflow(
            "Q2 Launch", "Drive signups", 15000, ["google_ads", "email", "seo"]
        )
        assert len(wf.tasks) == 9

    def test_compliance_audit_workflow(self):
        wf = create_compliance_audit_workflow("quarterly")
        assert len(wf.tasks) == 5


class TestWorkflowEngine:
    @pytest.mark.asyncio
    async def test_tick_dispatches_ready_tasks(self):
        engine = WorkflowEngine()
        wf = (
            TaskGraphBuilder("Test")
            .task("a", "agent-1")
            .task("b", "agent-2")
            .build()
        )
        engine.register_workflow(wf)
        dispatches = await engine.tick()
        assert len(dispatches) == 2

    @pytest.mark.asyncio
    async def test_tick_respects_dependencies(self):
        engine = WorkflowEngine()
        wf = (
            TaskGraphBuilder("Test")
            .task("a", "agent-1")
            .task("b", "agent-2", blocked_by=["a"])
            .build()
        )
        engine.register_workflow(wf)
        dispatches = await engine.tick()
        assert len(dispatches) == 1
        assert dispatches[0]["task_name"] == "a"

    @pytest.mark.asyncio
    async def test_workflow_completes(self):
        engine = WorkflowEngine()
        wf = (
            TaskGraphBuilder("Test")
            .task("a", "agent-1")
            .build()
        )
        engine.register_workflow(wf)
        await engine.tick()
        task = list(wf.tasks.values())[0]
        task.status = TaskStatus.COMPLETED
        await engine.tick()
        assert wf.status == WorkflowStatus.COMPLETED

    def test_progress_report(self):
        engine = WorkflowEngine()
        wf = create_lead_qualification_workflow(
            "Test Lead", "test@example.com", "TestCo", "Client"
        )
        engine.register_workflow(wf)
        report = engine.get_progress_report(wf.workflow_id)
        assert report["workflow"] == wf.name
        assert report["progress"]["total"] == 5
