"""
Workflow engine and primitives for Helios OS platform.

Generic workflow infrastructure used by all companies. Company-specific
workflow templates live in src/companies/<company>/workflows.py.

LeadForge templates are re-exported here for backward compatibility.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Workflow primitives
# ---------------------------------------------------------------------------

class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskPriority(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class WorkflowTask:
    """A single task in a workflow's task graph."""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    assigned_agent: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    budget_tokens: int = 100_000
    deadline: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = 3
    result: str | None = None
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)
    checkpoint: dict | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_ready(self) -> bool:
        """A task is ready when all blocking tasks are completed."""
        return self.status == TaskStatus.PENDING and not self.blocked_by

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


@dataclass
class WorkflowDefinition:
    """Complete definition of a business process workflow."""
    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    workflow_type: str = ""  # operational | project | administrative | incident
    status: WorkflowStatus = WorkflowStatus.PENDING
    tasks: dict[str, WorkflowTask] = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.MEDIUM
    initiator_agent: str = ""
    initiator_department: str = ""
    parent_workflow_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    def add_task(self, task: WorkflowTask) -> str:
        self.tasks[task.task_id] = task
        return task.task_id

    def get_ready_tasks(self) -> list[WorkflowTask]:
        """Return all tasks that are ready to execute (unblocked and pending)."""
        ready = []
        completed_ids = {
            tid for tid, t in self.tasks.items() if t.status == TaskStatus.COMPLETED
        }
        for task in self.tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            if all(bid in completed_ids for bid in task.blocked_by):
                ready.append(task)
        return ready

    def is_complete(self) -> bool:
        return all(t.status == TaskStatus.COMPLETED for t in self.tasks.values())

    def has_failures(self) -> bool:
        return any(t.status == TaskStatus.FAILED for t in self.tasks.values())

    def get_progress(self) -> dict[str, int]:
        counts = {s.value: 0 for s in TaskStatus}
        for task in self.tasks.values():
            counts[task.status.value] += 1
        counts["total"] = len(self.tasks)
        return counts


# ---------------------------------------------------------------------------
# Task Graph Builder
# ---------------------------------------------------------------------------

class TaskGraphBuilder:
    """Fluent builder for constructing task dependency graphs."""

    def __init__(self, workflow_name: str, workflow_type: str = "project"):
        self._workflow = WorkflowDefinition(
            name=workflow_name,
            workflow_type=workflow_type,
        )
        self._task_names: dict[str, str] = {}  # name -> task_id

    def task(
        self,
        name: str,
        agent: str,
        description: str = "",
        priority: TaskPriority = TaskPriority.MEDIUM,
        budget_tokens: int = 100_000,
        blocked_by: list[str] | None = None,
    ) -> TaskGraphBuilder:
        task = WorkflowTask(
            name=name,
            description=description,
            assigned_agent=agent,
            priority=priority,
            budget_tokens=budget_tokens,
        )

        # Resolve blocked_by names to task IDs
        if blocked_by:
            for blocker_name in blocked_by:
                if blocker_name in self._task_names:
                    blocker_id = self._task_names[blocker_name]
                    task.blocked_by.append(blocker_id)
                    # Update the blocker's "blocks" list
                    self._workflow.tasks[blocker_id].blocks.append(task.task_id)

        self._task_names[name] = task.task_id
        self._workflow.add_task(task)
        return self

    def build(self) -> WorkflowDefinition:
        return self._workflow


# ---------------------------------------------------------------------------
# LeadForge workflow templates (backward-compatible re-exports)
# ---------------------------------------------------------------------------

try:
    from src.companies.leadforge.workflows import (
        create_leadforge_sales_workflow,
        create_financial_reporting_workflow,
        create_marketing_campaign_workflow,
        create_compliance_audit_workflow,
        create_lead_qualification_workflow,
        create_lead_nurture_workflow,
        create_outbound_campaign_workflow,
        create_abm_campaign_workflow,
        create_client_onboarding_workflow,
    )
except ImportError:  # company packs ship with the enterprise distribution
    pass


# ---------------------------------------------------------------------------
# Workflow Engine (execution coordinator)
# ---------------------------------------------------------------------------

class WorkflowEngine:
    """
    Coordinates workflow execution by tracking task graphs and dispatching
    ready tasks to agents via the AgentInvoker.

    In production, this logic lives inside Temporal Workflows.
    This implementation provides a standalone executor for testing.
    """

    def __init__(self, invoker=None):
        self._invoker = invoker
        self._workflows: dict[str, WorkflowDefinition] = {}
        self._completed_workflows: list[str] = []

    def register_workflow(self, workflow: WorkflowDefinition) -> str:
        self._workflows[workflow.workflow_id] = workflow
        workflow.status = WorkflowStatus.RUNNING
        logger.info("Registered workflow: %s (%s)", workflow.name, workflow.workflow_id)
        return workflow.workflow_id

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        return self._workflows.get(workflow_id)

    def list_workflows(self, status: WorkflowStatus | None = None) -> list[WorkflowDefinition]:
        workflows = list(self._workflows.values())
        if status:
            workflows = [w for w in workflows if w.status == status]
        return workflows

    async def tick(self) -> list[dict]:
        """
        Execute one tick of the workflow engine.
        Finds all ready tasks across all running workflows and dispatches them
        IN PARALLEL for maximum throughput.
        Returns a list of dispatch records.

        In production, Temporal handles this automatically via its task queue.
        """
        import asyncio

        # Phase 1: Collect all ready tasks across all workflows
        ready_items: list[tuple] = []  # (workflow, task)
        dispatches = []

        for workflow in self._workflows.values():
            if workflow.status != WorkflowStatus.RUNNING:
                continue

            ready_tasks = workflow.get_ready_tasks()
            for task in ready_tasks:
                task.status = TaskStatus.IN_PROGRESS
                task.started_at = datetime.now(timezone.utc)
                task.attempt_count += 1

                dispatch = {
                    "workflow_id": workflow.workflow_id,
                    "workflow_name": workflow.name,
                    "task_id": task.task_id,
                    "task_name": task.name,
                    "agent": task.assigned_agent,
                    "priority": task.priority.value,
                }
                dispatches.append(dispatch)
                ready_items.append((workflow, task))

                logger.info(
                    "DISPATCH | %s | %s -> %s",
                    workflow.name, task.name, task.assigned_agent,
                )

        # Phase 2: Execute all ready tasks in parallel
        if ready_items and self._invoker:
            from src.core.agent_invoker import TaskMetadata

            async def _invoke_task(workflow, task):
                try:
                    meta = TaskMetadata(
                        task_id=task.task_id,
                        parent_task_id=workflow.workflow_id,
                        priority=task.priority.value,
                        budget_tokens=task.budget_tokens,
                    )
                    result = await self._invoker.invoke(
                        agent_id=task.assigned_agent,
                        prompt=task.description,
                        task_metadata=meta,
                    )
                    task.result = result.result
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = datetime.now(timezone.utc)
                    task.artifacts = result.artifacts
                except Exception as e:
                    task.error = str(e)
                    if task.attempt_count >= task.max_attempts:
                        task.status = TaskStatus.FAILED
                    else:
                        task.status = TaskStatus.PENDING  # Will retry on next tick

            # Dispatch all tasks concurrently
            await asyncio.gather(*[
                _invoke_task(wf, task) for wf, task in ready_items
            ], return_exceptions=True)

        # Phase 3: Check workflow completion status
        for workflow in self._workflows.values():
            if workflow.status != WorkflowStatus.RUNNING:
                continue

            if workflow.is_complete():
                workflow.status = WorkflowStatus.COMPLETED
                workflow.completed_at = datetime.now(timezone.utc)
                self._completed_workflows.append(workflow.workflow_id)
                logger.info("WORKFLOW COMPLETE | %s", workflow.name)
            elif workflow.has_failures():
                non_failed = [t for t in workflow.tasks.values() if t.status != TaskStatus.FAILED]
                if all(t.status == TaskStatus.COMPLETED for t in non_failed):
                    workflow.status = WorkflowStatus.FAILED
                    logger.warning("WORKFLOW FAILED | %s", workflow.name)

        return dispatches

    async def run_to_completion(
        self,
        workflow_id: str,
        max_ticks: int = 100,
        tick_delay: float = 0.1,
    ) -> WorkflowDefinition:
        """Run a workflow to completion (or failure)."""
        for _ in range(max_ticks):
            await self.tick()
            workflow = self._workflows.get(workflow_id)
            if not workflow:
                break
            if workflow.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED):
                return workflow
            await asyncio.sleep(tick_delay)

        return self._workflows.get(workflow_id)

    def get_progress_report(self, workflow_id: str) -> dict:
        """Get a human-readable progress report for a workflow."""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return {"error": "Workflow not found"}

        progress = workflow.get_progress()
        tasks_detail = []
        for task in workflow.tasks.values():
            tasks_detail.append({
                "name": task.name,
                "agent": task.assigned_agent,
                "status": task.status.value,
                "duration": task.duration_seconds,
                "result": task.result[:100] if task.result else None,
                "error": task.error,
            })

        return {
            "workflow": workflow.name,
            "status": workflow.status.value,
            "progress": progress,
            "tasks": tasks_detail,
        }
