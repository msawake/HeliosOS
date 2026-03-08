"""
Temporal Workflow and Activity definitions for all company business processes.

Each workflow represents a complete business process that may span multiple agents,
departments, and time periods. Temporal provides durable execution — if any agent
crashes mid-task, the workflow resumes exactly where it left off.

Workflow types:
1. Operational workflows (continuous: support tickets, invoice processing)
2. Project workflows (bounded: feature development, marketing campaigns)
3. Administrative workflows (periodic: financial reporting, compliance audits)
4. Incident workflows (event-driven: bug fixes, security incidents)
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
# Pre-built workflow templates
# ---------------------------------------------------------------------------

def create_bug_fix_workflow(
    bug_title: str,
    bug_description: str,
    reporter: str = "customer",
    severity: str = "high",
) -> WorkflowDefinition:
    """
    Complete bug fix workflow from report to resolution.
    ~40 minutes, zero human involvement for non-critical bugs.
    """
    builder = TaskGraphBuilder(f"Bug Fix: {bug_title}", "incident")

    builder.task(
        name="triage",
        agent="cs-tier1" if reporter == "customer" else "eng-lead",
        description=f"Triage bug report: {bug_description}\nSeverity: {severity}\nReporter: {reporter}",
        priority=TaskPriority.HIGH,
        budget_tokens=20_000,
    ).task(
        name="reproduce",
        agent="eng-qa",
        description=f"Reproduce the bug in staging environment.\nBug: {bug_title}\n{bug_description}",
        priority=TaskPriority.HIGH,
        budget_tokens=50_000,
        blocked_by=["triage"],
    ).task(
        name="root_cause",
        agent="eng-backend",
        description=f"Identify root cause of: {bug_title}\nUse git log, grep, and code analysis.",
        priority=TaskPriority.HIGH,
        budget_tokens=80_000,
        blocked_by=["reproduce"],
    ).task(
        name="implement_fix",
        agent="eng-backend",
        description=f"Implement fix for: {bug_title}\nFollow existing code patterns. Include inline comments only where non-obvious.",
        priority=TaskPriority.HIGH,
        budget_tokens=150_000,
        blocked_by=["root_cause"],
    ).task(
        name="write_test",
        agent="eng-qa",
        description=f"Write regression test that would have caught: {bug_title}",
        priority=TaskPriority.HIGH,
        budget_tokens=80_000,
        blocked_by=["root_cause"],
    ).task(
        name="code_review",
        agent="eng-reviewer",
        description="Review the bug fix PR for correctness, security, and performance.",
        priority=TaskPriority.HIGH,
        budget_tokens=50_000,
        blocked_by=["implement_fix", "write_test"],
    ).task(
        name="deploy",
        agent="eng-infra",
        description="Deploy the bug fix to production. Monitor for errors post-deploy.",
        priority=TaskPriority.HIGH,
        budget_tokens=30_000,
        blocked_by=["code_review"],
    ).task(
        name="customer_response",
        agent="cs-tier1",
        description=f"Draft and send resolution email to {reporter} about: {bug_title}",
        priority=TaskPriority.MEDIUM,
        budget_tokens=20_000,
        blocked_by=["root_cause"],  # Can start once root cause is known
    )

    workflow = builder.build()
    workflow.priority = TaskPriority(severity) if severity in [p.value for p in TaskPriority] else TaskPriority.HIGH
    workflow.metadata = {
        "bug_title": bug_title,
        "reporter": reporter,
        "severity": severity,
    }
    return workflow


def create_feature_workflow(
    feature_name: str,
    feature_description: str,
    requested_by: str = "prod-lead",
) -> WorkflowDefinition:
    """
    Complete feature development workflow from spec to launch.
    Spans days to weeks depending on complexity.
    """
    builder = TaskGraphBuilder(f"Feature: {feature_name}", "project")

    builder.task(
        name="requirements",
        agent="prod-lead",
        description=f"Create detailed requirements for: {feature_name}\n{feature_description}\nInclude acceptance criteria.",
        budget_tokens=100_000,
    ).task(
        name="research",
        agent="prod-researcher",
        description=f"Research competitive implementations and best practices for: {feature_name}",
        budget_tokens=80_000,
    ).task(
        name="design",
        agent="prod-designer",
        description=f"Create UI/UX specification for: {feature_name}",
        budget_tokens=80_000,
        blocked_by=["requirements", "research"],
    ).task(
        name="technical_design",
        agent="eng-lead",
        description=f"Create technical design document for: {feature_name}\nInclude API contracts, data models, architecture decisions.",
        budget_tokens=120_000,
        blocked_by=["requirements"],
    ).task(
        name="backend_impl",
        agent="eng-backend",
        description=f"Implement backend for: {feature_name}\nFollow technical design.",
        budget_tokens=300_000,
        blocked_by=["technical_design"],
    ).task(
        name="frontend_impl",
        agent="eng-frontend",
        description=f"Implement frontend for: {feature_name}\nFollow design spec.",
        budget_tokens=250_000,
        blocked_by=["design", "technical_design"],
    ).task(
        name="api_docs",
        agent="eng-docs",
        description=f"Write API documentation for: {feature_name}",
        budget_tokens=50_000,
        blocked_by=["backend_impl"],
    ).task(
        name="integration_test",
        agent="eng-qa",
        description=f"Write and run integration tests for: {feature_name}",
        budget_tokens=100_000,
        blocked_by=["backend_impl", "frontend_impl"],
    ).task(
        name="security_review",
        agent="eng-security",
        description=f"Security review for: {feature_name}",
        budget_tokens=80_000,
        blocked_by=["backend_impl", "frontend_impl"],
    ).task(
        name="code_review",
        agent="eng-reviewer",
        description=f"Full code review for: {feature_name}",
        budget_tokens=80_000,
        blocked_by=["integration_test", "security_review"],
    ).task(
        name="canary_deploy",
        agent="eng-infra",
        description=f"Deploy {feature_name} to 10% canary. Monitor for 24h.",
        budget_tokens=50_000,
        blocked_by=["code_review"],
    ).task(
        name="full_rollout",
        agent="eng-infra",
        description=f"Full rollout of {feature_name} to 100%.",
        budget_tokens=30_000,
        blocked_by=["canary_deploy"],
    ).task(
        name="launch_content",
        agent="mkt-content",
        description=f"Write blog post and launch announcement for: {feature_name}",
        budget_tokens=80_000,
        blocked_by=["requirements"],
    ).task(
        name="launch_email",
        agent="mkt-email",
        description=f"Send launch announcement email to customers about: {feature_name}",
        budget_tokens=30_000,
        blocked_by=["full_rollout", "launch_content"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "feature_name": feature_name,
        "requested_by": requested_by,
    }
    return workflow


def create_customer_onboarding_workflow(
    customer_name: str,
    customer_email: str,
    plan: str = "standard",
) -> WorkflowDefinition:
    """Customer onboarding from signup to active usage."""
    builder = TaskGraphBuilder(f"Onboard: {customer_name}", "operational")

    builder.task(
        name="welcome_email",
        agent="cs-success",
        description=f"Send welcome email to {customer_name} ({customer_email}). Plan: {plan}.",
        priority=TaskPriority.HIGH,
        budget_tokens=15_000,
    ).task(
        name="account_setup",
        agent="eng-backend",
        description=f"Provision account for {customer_name}. Plan: {plan}. Set up default configurations.",
        priority=TaskPriority.HIGH,
        budget_tokens=30_000,
    ).task(
        name="billing_setup",
        agent="fin-ar",
        description=f"Set up billing for {customer_name}. Email: {customer_email}. Plan: {plan}.",
        priority=TaskPriority.HIGH,
        budget_tokens=20_000,
        blocked_by=["account_setup"],
    ).task(
        name="onboarding_guide",
        agent="cs-success",
        description=f"Send onboarding guide and schedule kickoff call with {customer_name}.",
        budget_tokens=20_000,
        blocked_by=["welcome_email", "account_setup"],
    ).task(
        name="health_check",
        agent="cs-success",
        description=f"30-day health check for {customer_name}. Review usage, identify blockers.",
        budget_tokens=25_000,
        blocked_by=["onboarding_guide"],
    )

    workflow = builder.build()
    workflow.metadata = {"customer_name": customer_name, "plan": plan}
    return workflow


def create_sales_pipeline_workflow(
    lead_name: str,
    lead_email: str,
    lead_source: str = "inbound",
    company: str = "",
) -> WorkflowDefinition:
    """Sales pipeline from lead to close."""
    builder = TaskGraphBuilder(f"Sales: {lead_name}", "operational")

    builder.task(
        name="qualify",
        agent="sales-sdr",
        description=f"Qualify lead: {lead_name} ({lead_email}) from {lead_source}. Company: {company}.",
        priority=TaskPriority.HIGH,
        budget_tokens=20_000,
    ).task(
        name="research",
        agent="sales-sdr",
        description=f"Research {company} and {lead_name}. Find pain points, budget signals, decision makers.",
        budget_tokens=30_000,
    ).task(
        name="outreach",
        agent="sales-sdr",
        description=f"Send personalized outreach to {lead_name} at {lead_email}.",
        budget_tokens=15_000,
        blocked_by=["qualify", "research"],
    ).task(
        name="demo_prep",
        agent="sales-ae",
        description=f"Prepare demo for {lead_name}/{company}. Customize to their use case.",
        budget_tokens=40_000,
        blocked_by=["outreach"],
    ).task(
        name="proposal",
        agent="sales-ae",
        description=f"Create proposal for {company}. Include pricing, timeline, and ROI projection.",
        budget_tokens=50_000,
        blocked_by=["demo_prep"],
    ).task(
        name="contract",
        agent="legal-contracts",
        description=f"Draft contract for {company}. Standard terms. DRAFT — requires human review.",
        budget_tokens=40_000,
        blocked_by=["proposal"],
    ).task(
        name="crm_update",
        agent="sales-ops",
        description=f"Update CRM with deal progress for {lead_name}/{company}.",
        budget_tokens=10_000,
        blocked_by=["qualify"],
    )

    workflow = builder.build()
    workflow.metadata = {"lead_name": lead_name, "company": company, "source": lead_source}
    return workflow


def create_financial_reporting_workflow(
    period: str = "monthly",
    period_end: str = "",
) -> WorkflowDefinition:
    """Periodic financial reporting workflow."""
    builder = TaskGraphBuilder(f"Finance Report: {period} {period_end}", "administrative")

    builder.task(
        name="revenue_report",
        agent="fin-ar",
        description=f"Generate {period} revenue report for period ending {period_end}.",
        budget_tokens=40_000,
    ).task(
        name="expense_report",
        agent="fin-ap",
        description=f"Generate {period} expense report for period ending {period_end}.",
        budget_tokens=40_000,
    ).task(
        name="pnl_statement",
        agent="fin-reporting",
        description=f"Create P&L statement for {period} ending {period_end}.",
        budget_tokens=60_000,
        blocked_by=["revenue_report", "expense_report"],
    ).task(
        name="balance_sheet",
        agent="fin-reporting",
        description=f"Create balance sheet as of {period_end}.",
        budget_tokens=50_000,
        blocked_by=["revenue_report", "expense_report"],
    ).task(
        name="cash_flow",
        agent="fin-reporting",
        description=f"Create cash flow statement for {period} ending {period_end}.",
        budget_tokens=50_000,
        blocked_by=["pnl_statement", "balance_sheet"],
    ).task(
        name="cfo_review",
        agent="exec-cfo",
        description=f"Review and approve {period} financial statements for {period_end}.",
        budget_tokens=30_000,
        blocked_by=["cash_flow"],
    )

    workflow = builder.build()
    workflow.metadata = {"period": period, "period_end": period_end}
    return workflow


def create_hiring_workflow(
    role_title: str,
    department: str,
    skills: list[str],
    budget_range: str = "",
) -> WorkflowDefinition:
    """Contractor hiring from need identification to onboarding."""
    builder = TaskGraphBuilder(f"Hire: {role_title}", "project")

    builder.task(
        name="budget_approval",
        agent="exec-cfo",
        description=f"Approve budget for hiring {role_title} in {department}. Range: {budget_range}.",
        priority=TaskPriority.HIGH,
        budget_tokens=20_000,
    ).task(
        name="job_description",
        agent="hr-recruiter",
        description=f"Draft job description for {role_title}. Skills: {', '.join(skills)}. Dept: {department}.",
        budget_tokens=25_000,
        blocked_by=["budget_approval"],
    ).task(
        name="sourcing",
        agent="hr-recruiter",
        description=f"Source candidates for {role_title}. Post to relevant platforms.",
        budget_tokens=30_000,
        blocked_by=["job_description"],
    ).task(
        name="screening",
        agent="hr-recruiter",
        description=f"Screen applications for {role_title}. Evaluate against requirements.",
        budget_tokens=50_000,
        blocked_by=["sourcing"],
    ).task(
        name="technical_assessment",
        agent="eng-lead" if department == "engineering" else "hr-recruiter",
        description=f"Conduct technical assessment for shortlisted {role_title} candidates.",
        budget_tokens=60_000,
        blocked_by=["screening"],
    ).task(
        name="offer_draft",
        agent="hr-recruiter",
        description=f"Draft offer for selected {role_title} candidate. REQUIRES HUMAN APPROVAL.",
        budget_tokens=20_000,
        blocked_by=["technical_assessment"],
    ).task(
        name="contract_draft",
        agent="legal-contracts",
        description=f"Draft contractor agreement for {role_title}. REQUIRES HUMAN REVIEW.",
        budget_tokens=40_000,
        blocked_by=["offer_draft"],
    ).task(
        name="onboarding",
        agent="hr-onboarding",
        description=f"Onboard new {role_title}: accounts, access, training materials.",
        budget_tokens=30_000,
        blocked_by=["contract_draft"],
    )

    workflow = builder.build()
    workflow.priority = TaskPriority.HIGH
    workflow.metadata = {"role": role_title, "department": department, "skills": skills}
    return workflow


def create_incident_response_workflow(
    incident_title: str,
    incident_description: str,
    severity: str = "high",
) -> WorkflowDefinition:
    """Security or production incident response."""
    builder = TaskGraphBuilder(f"Incident: {incident_title}", "incident")

    prio = TaskPriority.CRITICAL if severity == "critical" else TaskPriority.HIGH

    builder.task(
        name="assess",
        agent="ops-monitoring",
        description=f"Assess incident: {incident_title}\n{incident_description}\nDetermine scope and impact.",
        priority=prio,
        budget_tokens=30_000,
    ).task(
        name="notify_stakeholders",
        agent="ops-lead",
        description=f"Notify stakeholders about incident: {incident_title}. Severity: {severity}.",
        priority=prio,
        budget_tokens=15_000,
        blocked_by=["assess"],
    ).task(
        name="contain",
        agent="eng-infra",
        description=f"Contain incident: {incident_title}. Minimize blast radius.",
        priority=prio,
        budget_tokens=80_000,
        blocked_by=["assess"],
    ).task(
        name="investigate",
        agent="eng-security" if "security" in incident_title.lower() else "eng-backend",
        description=f"Investigate root cause of: {incident_title}",
        priority=prio,
        budget_tokens=100_000,
        blocked_by=["contain"],
    ).task(
        name="remediate",
        agent="eng-backend",
        description=f"Implement remediation for: {incident_title}",
        priority=prio,
        budget_tokens=150_000,
        blocked_by=["investigate"],
    ).task(
        name="verify",
        agent="eng-qa",
        description=f"Verify remediation is effective for: {incident_title}",
        priority=prio,
        budget_tokens=50_000,
        blocked_by=["remediate"],
    ).task(
        name="postmortem",
        agent="eng-lead",
        description=f"Write postmortem for: {incident_title}. Include timeline, root cause, action items.",
        budget_tokens=60_000,
        blocked_by=["verify"],
    ).task(
        name="customer_comms",
        agent="cs-lead",
        description=f"Draft customer communication about: {incident_title}. REQUIRES REVIEW for critical incidents.",
        budget_tokens=25_000,
        blocked_by=["contain"],
    )

    workflow = builder.build()
    workflow.priority = prio
    workflow.metadata = {"incident": incident_title, "severity": severity}
    return workflow


def create_marketing_campaign_workflow(
    campaign_name: str,
    campaign_goal: str,
    budget_usd: float,
    channels: list[str],
) -> WorkflowDefinition:
    """Marketing campaign from planning to execution and analysis."""
    builder = TaskGraphBuilder(f"Campaign: {campaign_name}", "project")

    builder.task(
        name="strategy",
        agent="mkt-lead",
        description=f"Define strategy for campaign: {campaign_name}\nGoal: {campaign_goal}\nBudget: ${budget_usd}\nChannels: {', '.join(channels)}",
        budget_tokens=60_000,
    ).task(
        name="budget_approval",
        agent="exec-cfo",
        description=f"Approve marketing budget ${budget_usd} for campaign: {campaign_name}",
        budget_tokens=15_000,
    ).task(
        name="content_creation",
        agent="mkt-content",
        description=f"Create content for campaign: {campaign_name}. Follow brand guidelines.",
        budget_tokens=100_000,
        blocked_by=["strategy", "budget_approval"],
    ).task(
        name="seo_optimization",
        agent="mkt-seo",
        description=f"SEO optimize campaign content for: {campaign_name}",
        budget_tokens=40_000,
        blocked_by=["content_creation"],
    ).task(
        name="email_campaign",
        agent="mkt-email",
        description=f"Set up email campaign for: {campaign_name}. Segment audience. A/B test subject lines.",
        budget_tokens=40_000,
        blocked_by=["content_creation"],
    ).task(
        name="launch",
        agent="mkt-lead",
        description=f"Launch campaign: {campaign_name} across all channels.",
        budget_tokens=20_000,
        blocked_by=["seo_optimization", "email_campaign"],
    ).task(
        name="week1_analysis",
        agent="mkt-analytics",
        description=f"Week 1 performance analysis for campaign: {campaign_name}",
        budget_tokens=40_000,
        blocked_by=["launch"],
    ).task(
        name="optimization",
        agent="mkt-lead",
        description=f"Optimize campaign: {campaign_name} based on week 1 data.",
        budget_tokens=30_000,
        blocked_by=["week1_analysis"],
    ).task(
        name="final_report",
        agent="mkt-analytics",
        description=f"Final ROI report for campaign: {campaign_name}",
        budget_tokens=50_000,
        blocked_by=["optimization"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "campaign": campaign_name,
        "budget_usd": budget_usd,
        "channels": channels,
    }
    return workflow


def create_compliance_audit_workflow(
    audit_type: str = "quarterly",
    focus_areas: list[str] | None = None,
) -> WorkflowDefinition:
    """Compliance audit workflow."""
    areas = focus_areas or ["data_privacy", "financial", "security", "employment"]
    builder = TaskGraphBuilder(f"Compliance Audit: {audit_type}", "administrative")

    builder.task(
        name="scope_definition",
        agent="legal-compliance",
        description=f"{audit_type.title()} compliance audit. Focus: {', '.join(areas)}. Define scope and checklist.",
        budget_tokens=40_000,
    ).task(
        name="data_privacy_review",
        agent="legal-compliance",
        description="Review data privacy compliance: GDPR, CCPA, data retention policies.",
        budget_tokens=60_000,
        blocked_by=["scope_definition"],
    ).task(
        name="security_review",
        agent="eng-security",
        description="Security compliance review: access controls, encryption, vulnerability management.",
        budget_tokens=80_000,
        blocked_by=["scope_definition"],
    ).task(
        name="financial_review",
        agent="fin-tax",
        description="Financial compliance review: tax obligations, reporting accuracy, audit trail integrity.",
        budget_tokens=60_000,
        blocked_by=["scope_definition"],
    ).task(
        name="compile_report",
        agent="legal-lead",
        description=f"Compile {audit_type} compliance report. DRAFT — requires human legal review.",
        budget_tokens=50_000,
        blocked_by=["data_privacy_review", "security_review", "financial_review"],
    )

    workflow = builder.build()
    workflow.metadata = {"audit_type": audit_type, "focus_areas": areas}
    return workflow


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
        Finds all ready tasks across all running workflows and dispatches them.
        Returns a list of dispatch records.

        In production, Temporal handles this automatically via its task queue.
        """
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

                logger.info(
                    "DISPATCH | %s | %s -> %s",
                    workflow.name, task.name, task.assigned_agent,
                )

                # Execute via invoker if available
                if self._invoker:
                    try:
                        from src.core.agent_invoker import TaskMetadata
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

            # Check if workflow is complete
            if workflow.is_complete():
                workflow.status = WorkflowStatus.COMPLETED
                workflow.completed_at = datetime.now(timezone.utc)
                self._completed_workflows.append(workflow.workflow_id)
                logger.info("WORKFLOW COMPLETE | %s", workflow.name)
            elif workflow.has_failures():
                # Check if all non-failed tasks are complete
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
