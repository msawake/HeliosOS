"""Tests for workflow definitions and the workflow engine."""

import pytest
from src.workflows.definitions import (
    TaskGraphBuilder,
    TaskPriority,
    TaskStatus,
    WorkflowEngine,
    WorkflowStatus,
    create_bug_fix_workflow,
    create_compliance_audit_workflow,
    create_customer_onboarding_workflow,
    create_feature_workflow,
    create_financial_reporting_workflow,
    create_hiring_workflow,
    create_incident_response_workflow,
    create_marketing_campaign_workflow,
    create_sales_pipeline_workflow,
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
        assert len(ready) == 3  # a, b, c are all ready
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
        # Complete task a
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
    def test_bug_fix_workflow(self):
        wf = create_bug_fix_workflow(
            "Payment failure",
            "Payments fail on checkout",
            reporter="customer",
            severity="high",
        )
        assert len(wf.tasks) == 8
        assert wf.workflow_type == "incident"
        # Triage should be the first ready task
        ready = wf.get_ready_tasks()
        assert any(t.name == "triage" for t in ready)

    def test_feature_workflow(self):
        wf = create_feature_workflow(
            "AI Search",
            "Add semantic search",
        )
        assert len(wf.tasks) == 14
        assert wf.workflow_type == "project"
        # Requirements and research should be ready first
        ready = wf.get_ready_tasks()
        ready_names = {t.name for t in ready}
        assert "requirements" in ready_names
        assert "research" in ready_names

    def test_customer_onboarding_workflow(self):
        wf = create_customer_onboarding_workflow("Acme Corp", "acme@corp.com")
        assert len(wf.tasks) == 5

    def test_sales_pipeline_workflow(self):
        wf = create_sales_pipeline_workflow("John", "john@acme.com", company="Acme")
        assert len(wf.tasks) == 7

    def test_financial_reporting_workflow(self):
        wf = create_financial_reporting_workflow("monthly", "2026-02-28")
        assert len(wf.tasks) == 6

    def test_hiring_workflow(self):
        wf = create_hiring_workflow("Backend Engineer", "engineering", ["Python", "PostgreSQL"])
        assert len(wf.tasks) == 8

    def test_incident_response_workflow(self):
        wf = create_incident_response_workflow("Data breach", "Unauthorized access detected")
        assert len(wf.tasks) == 8

    def test_marketing_campaign_workflow(self):
        wf = create_marketing_campaign_workflow(
            "Q2 Launch", "Drive signups", 15000, ["email", "blog"]
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

        # First tick: only a should dispatch
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

        # Tick to dispatch
        await engine.tick()

        # Manually complete the task
        task = list(wf.tasks.values())[0]
        task.status = TaskStatus.COMPLETED

        # Tick to check completion
        await engine.tick()
        assert wf.status == WorkflowStatus.COMPLETED

    def test_progress_report(self):
        engine = WorkflowEngine()
        wf = create_bug_fix_workflow("Test bug", "Description")
        engine.register_workflow(wf)
        report = engine.get_progress_report(wf.workflow_id)
        assert report["workflow"] == wf.name
        assert report["progress"]["total"] == 8
