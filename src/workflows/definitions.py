"""
Temporal Workflow and Activity definitions for LeadForge AI business processes.

Each workflow represents a complete business process that may span multiple agents,
departments, and time periods. Temporal provides durable execution — if any agent
crashes mid-task, the workflow resumes exactly where it left off.

Workflow types:
1. Operational workflows (continuous: lead qualification, client onboarding)
2. Project workflows (bounded: outbound campaigns, ABM campaigns)
3. Administrative workflows (periodic: financial reporting, compliance audits)
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

def create_leadforge_sales_workflow(
    lead_name: str,
    lead_email: str,
    lead_source: str = "inbound",
    company: str = "",
) -> WorkflowDefinition:
    """LeadForge selling its own services — full sales pipeline from lead to close."""
    builder = TaskGraphBuilder(f"LeadForge Sales: {lead_name}", "operational")

    builder.task(
        name="qualify",
        agent="sales-sdr",
        description=f"Qualify inbound lead {lead_name} ({lead_email}) from {lead_source} for LeadForge services. Company: {company}.",
        priority=TaskPriority.HIGH,
        budget_tokens=20_000,
    ).task(
        name="research",
        agent="sales-sdr",
        description=f"Research {company} and {lead_name}. Identify current lead-gen stack, pain points, budget signals, and key decision makers.",
        budget_tokens=30_000,
    ).task(
        name="outreach",
        agent="sales-sdr",
        description=f"Send personalized outreach to {lead_name} at {lead_email} highlighting LeadForge capabilities relevant to {company}.",
        budget_tokens=15_000,
        blocked_by=["qualify", "research"],
    ).task(
        name="demo_prep",
        agent="sales-ae",
        description=f"Prepare a tailored LeadForge platform demo for {lead_name} at {company}. Customize to their ICP, industry verticals, and outreach channels.",
        budget_tokens=40_000,
        blocked_by=["outreach"],
    ).task(
        name="proposal",
        agent="sales-ae",
        description=f"Create proposal for {company}. Include LeadForge pricing tiers, projected lead volume, timeline, and ROI projection.",
        budget_tokens=50_000,
        blocked_by=["demo_prep"],
    ).task(
        name="contract_review",
        agent="legal-lead",
        description=f"Review and finalize service agreement for {company}. Ensure SLA terms, data-handling clauses, and payment schedule are correct. DRAFT — requires human review.",
        budget_tokens=40_000,
        blocked_by=["proposal"],
    ).task(
        name="crm_update",
        agent="sales-ops",
        description=f"Update CRM with full deal lifecycle for {lead_name} at {company}. Log qualification details, outreach history, proposal, and contract status.",
        budget_tokens=10_000,
        blocked_by=["qualify"],
    )

    workflow = builder.build()
    workflow.metadata = {"lead_name": lead_name, "company": company, "source": lead_source}
    return workflow


def create_financial_reporting_workflow(
    period: str = "monthly",
) -> WorkflowDefinition:
    """Periodic financial reporting workflow for LeadForge AI."""
    builder = TaskGraphBuilder(f"Finance Report: {period}", "administrative")

    builder.task(
        name="revenue_report",
        agent="fin-ar",
        description=f"Generate {period} revenue report covering all LeadForge client retainers, usage-based billing, and one-time project fees.",
        budget_tokens=40_000,
    ).task(
        name="expense_summary",
        agent="fin-lead",
        description=f"Compile {period} expense summary including payroll, SaaS tooling, ad spend, and infrastructure costs.",
        budget_tokens=40_000,
    ).task(
        name="consolidated_report",
        agent="fin-lead",
        description=f"Consolidate {period} revenue and expense data into a unified financial report with margin analysis and variance commentary.",
        budget_tokens=60_000,
        blocked_by=["revenue_report", "expense_summary"],
    ).task(
        name="cfo_review",
        agent="exec-cfo",
        description=f"Review and approve the {period} consolidated financial report. Flag any anomalies or budget overruns for leadership discussion.",
        budget_tokens=30_000,
        blocked_by=["consolidated_report"],
    )

    workflow = builder.build()
    workflow.metadata = {"period": period}
    return workflow


def create_marketing_campaign_workflow(
    campaign_name: str,
    campaign_goal: str,
    budget_usd: float,
    channels: list[str],
) -> WorkflowDefinition:
    """Marketing campaign from planning to execution and analysis for LeadForge AI."""
    builder = TaskGraphBuilder(f"Campaign: {campaign_name}", "project")

    builder.task(
        name="strategy",
        agent="mkt-lead",
        description=f"Define strategy for campaign: {campaign_name}\nGoal: {campaign_goal}\nBudget: ${budget_usd}\nChannels: {', '.join(channels)}",
        budget_tokens=60_000,
    ).task(
        name="demand_gen_plan",
        agent="mkt-demandgen",
        description=f"Create demand generation plan for campaign: {campaign_name}. Define target audience segments, funnel stages, and conversion targets aligned with goal: {campaign_goal}.",
        budget_tokens=50_000,
        blocked_by=["strategy"],
    ).task(
        name="ad_setup",
        agent="mkt-ppc",
        description=f"Set up paid ad campaigns for: {campaign_name}. Configure targeting, bidding strategy, and creatives across channels: {', '.join(channels)}. Budget: ${budget_usd}.",
        budget_tokens=50_000,
        blocked_by=["demand_gen_plan"],
    ).task(
        name="content",
        agent="mkt-content",
        description=f"Create content assets for campaign: {campaign_name}. Include landing pages, blog posts, and social copy. Follow LeadForge brand guidelines.",
        budget_tokens=100_000,
        blocked_by=["strategy"],
    ).task(
        name="seo",
        agent="mkt-seo",
        description=f"SEO optimize all campaign content for: {campaign_name}. Conduct keyword research, optimize meta tags, and ensure technical SEO readiness.",
        budget_tokens=40_000,
        blocked_by=["content"],
    ).task(
        name="email_sequence",
        agent="mkt-email",
        description=f"Build email nurture sequence for campaign: {campaign_name}. Segment audience, draft copy for each stage, and configure A/B tests on subject lines.",
        budget_tokens=40_000,
        blocked_by=["content"],
    ).task(
        name="launch",
        agent="mkt-lead",
        description=f"Launch campaign: {campaign_name} across all channels. Coordinate go-live of ads, content, SEO pages, and email sequences.",
        budget_tokens=20_000,
        blocked_by=["ad_setup", "content", "seo", "email_sequence"],
    ).task(
        name="analytics",
        agent="mkt-analytics",
        description=f"Analyze performance data for campaign: {campaign_name}. Track impressions, CTR, CPL, and pipeline contribution against goal: {campaign_goal}.",
        budget_tokens=40_000,
        blocked_by=["launch"],
    ).task(
        name="optimize",
        agent="mkt-lead",
        description=f"Optimize campaign: {campaign_name} based on analytics data. Reallocate budget across channels, adjust messaging, and update targeting.",
        budget_tokens=30_000,
        blocked_by=["analytics"],
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
    """Compliance audit workflow for LeadForge AI operations."""
    areas = focus_areas or ["outreach_regulations", "data_handling", "financial"]
    builder = TaskGraphBuilder(f"Compliance Audit: {audit_type}", "administrative")

    builder.task(
        name="outreach_compliance",
        agent="legal-compliance",
        description=f"{audit_type.title()} audit of outreach compliance. Review CAN-SPAM, TCPA, and GDPR adherence across all LeadForge client campaigns. Focus areas: {', '.join(areas)}.",
        budget_tokens=60_000,
    ).task(
        name="data_handling",
        agent="legal-compliance",
        description=f"{audit_type.title()} audit of data handling practices. Review prospect data storage, consent management, data retention policies, and third-party data processor agreements.",
        budget_tokens=60_000,
    ).task(
        name="financial_review",
        agent="fin-lead",
        description=f"{audit_type.title()} financial compliance review. Verify billing accuracy, tax obligations, revenue recognition, and audit trail integrity for all client accounts.",
        budget_tokens=60_000,
    ).task(
        name="report",
        agent="legal-lead",
        description=f"Compile {audit_type} compliance audit report consolidating outreach, data handling, and financial findings. DRAFT — requires human legal review.",
        budget_tokens=50_000,
        blocked_by=["outreach_compliance", "data_handling", "financial_review"],
    ).task(
        name="executive_review",
        agent="exec-coo",
        description=f"Executive review of {audit_type} compliance audit report. Approve remediation plan and sign off on compliance posture.",
        budget_tokens=30_000,
        blocked_by=["report"],
    )

    workflow = builder.build()
    workflow.metadata = {"audit_type": audit_type, "focus_areas": areas}
    return workflow


def create_lead_qualification_workflow(
    prospect_name: str,
    prospect_email: str,
    prospect_company: str,
    client_name: str,
    source: str = "inbound",
) -> WorkflowDefinition:
    """Lead qualification workflow — research, score, enrich, route, and initiate outreach for a prospect on behalf of a LeadForge client."""
    builder = TaskGraphBuilder(f"Lead Qualification: {prospect_name} for {client_name}", "operational")

    builder.task(
        name="research",
        agent="sales-researcher",
        description=f"Research prospect {prospect_name} ({prospect_email}) at {prospect_company}. Gather firmographic data, technographic signals, recent news, and social presence. Source: {source}. Client: {client_name}.",
        budget_tokens=40_000,
    ).task(
        name="score",
        agent="sales-scorer",
        description=f"Score prospect {prospect_name} at {prospect_company} based on research data. Apply {client_name}'s ICP criteria, engagement signals, and intent data to produce a lead score and tier.",
        budget_tokens=30_000,
        blocked_by=["research"],
    ).task(
        name="enrich_crm",
        agent="sales-ops",
        description=f"Enrich CRM record for {prospect_name} ({prospect_email}) at {prospect_company}. Append research findings, lead score, and source attribution ({source}) under client {client_name}.",
        budget_tokens=15_000,
        blocked_by=["score"],
    ).task(
        name="route_decision",
        agent="sales-lead",
        description=f"Determine routing for scored lead {prospect_name} at {prospect_company}. Decide whether to fast-track to SDR outreach, place into nurture sequence, or disqualify. Client: {client_name}.",
        budget_tokens=20_000,
        blocked_by=["score"],
    ).task(
        name="initial_outreach",
        agent="sales-sdr",
        description=f"Execute initial outreach to {prospect_name} ({prospect_email}) at {prospect_company} on behalf of {client_name}. Use personalized messaging based on research and routing decision.",
        budget_tokens=20_000,
        blocked_by=["route_decision"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "prospect_name": prospect_name,
        "prospect_email": prospect_email,
        "prospect_company": prospect_company,
        "client_name": client_name,
        "source": source,
    }
    return workflow


def create_lead_nurture_workflow(
    lead_name: str,
    lead_email: str,
    lead_company: str,
    client_name: str,
    nurture_reason: str = "MQL",
) -> WorkflowDefinition:
    """Lead nurture workflow — design and execute a nurture sequence to warm a lead back toward sales readiness."""
    builder = TaskGraphBuilder(f"Lead Nurture: {lead_name} for {client_name}", "operational")

    builder.task(
        name="design_sequence",
        agent="sales-nurture",
        description=f"Design nurture sequence for {lead_name} ({lead_email}) at {lead_company}. Reason for nurture: {nurture_reason}. Define touchpoints, cadence, and messaging themes tailored to {client_name}'s value proposition.",
        budget_tokens=40_000,
    ).task(
        name="create_content",
        agent="mkt-content",
        description=f"Create nurture content for {lead_name} at {lead_company}. Produce email copy, relevant case studies, and educational assets aligned with {client_name}'s brand and the nurture reason: {nurture_reason}.",
        budget_tokens=60_000,
        blocked_by=["design_sequence"],
    ).task(
        name="execute_sequence",
        agent="sales-nurture",
        description=f"Execute nurture sequence for {lead_name} ({lead_email}) at {lead_company}. Deploy emails and touchpoints per the designed cadence on behalf of {client_name}.",
        budget_tokens=30_000,
        blocked_by=["create_content"],
    ).task(
        name="monitor_engagement",
        agent="sales-scorer",
        description=f"Monitor engagement signals from {lead_name} at {lead_company} during nurture sequence. Track opens, clicks, content downloads, and website visits. Re-score lead based on engagement for {client_name}.",
        budget_tokens=25_000,
        blocked_by=["execute_sequence"],
    ).task(
        name="re_qualify",
        agent="sales-lead",
        description=f"Re-qualify {lead_name} at {lead_company} after nurture sequence completes. Based on updated engagement score, decide whether to advance to sales outreach, continue nurture, or archive. Client: {client_name}.",
        budget_tokens=20_000,
        blocked_by=["monitor_engagement"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "lead_name": lead_name,
        "lead_email": lead_email,
        "lead_company": lead_company,
        "client_name": client_name,
        "nurture_reason": nurture_reason,
    }
    return workflow


def create_outbound_campaign_workflow(
    client_name: str,
    campaign_name: str,
    target_icp: str,
    channels: list[str],
    daily_volume: int = 50,
) -> WorkflowDefinition:
    """Outbound campaign workflow — build and execute a targeted outbound campaign for a LeadForge client."""
    builder = TaskGraphBuilder(f"Outbound Campaign: {campaign_name} for {client_name}", "project")

    builder.task(
        name="icp_definition",
        agent="sales-lead",
        description=f"Refine and finalize ICP definition for {client_name}'s outbound campaign: {campaign_name}. Target ICP: {target_icp}. Define firmographic filters, persona criteria, and exclusion rules.",
        budget_tokens=40_000,
    ).task(
        name="prospect_list",
        agent="sales-researcher",
        description=f"Build prospect list for campaign: {campaign_name}. Source contacts matching ICP ({target_icp}) for {client_name}. Target daily outreach volume: {daily_volume}. Channels: {', '.join(channels)}.",
        budget_tokens=60_000,
        blocked_by=["icp_definition"],
    ).task(
        name="outreach_templates",
        agent="mkt-content",
        description=f"Create outreach templates for campaign: {campaign_name}. Write personalized email, LinkedIn, and multi-channel sequences for ICP: {target_icp}. Align with {client_name}'s brand voice. Channels: {', '.join(channels)}.",
        budget_tokens=80_000,
        blocked_by=["icp_definition"],
    ).task(
        name="score_list",
        agent="sales-scorer",
        description=f"Score and prioritize the prospect list for campaign: {campaign_name}. Rank prospects by fit score and intent signals for {client_name}'s ICP ({target_icp}).",
        budget_tokens=30_000,
        blocked_by=["prospect_list"],
    ).task(
        name="compliance_review",
        agent="legal-compliance",
        description=f"Review outreach templates for campaign: {campaign_name} for compliance with CAN-SPAM, GDPR, and {client_name}'s contractual obligations. Approve messaging before launch.",
        budget_tokens=30_000,
        blocked_by=["outreach_templates"],
    ).task(
        name="launch_outreach",
        agent="sales-sdr",
        description=f"Launch outbound outreach for campaign: {campaign_name} on behalf of {client_name}. Execute sequences at {daily_volume} contacts/day across channels: {', '.join(channels)}. Use scored prospect list and approved templates.",
        budget_tokens=40_000,
        blocked_by=["score_list", "compliance_review"],
    ).task(
        name="weekly_analysis",
        agent="mkt-analytics",
        description=f"Perform weekly analysis of campaign: {campaign_name} for {client_name}. Report on delivery rates, open rates, reply rates, positive response rates, and meetings booked by channel.",
        budget_tokens=40_000,
        blocked_by=["launch_outreach"],
    ).task(
        name="optimize",
        agent="sales-lead",
        description=f"Optimize campaign: {campaign_name} for {client_name} based on weekly analysis. Adjust targeting, messaging, channel mix, and daily volume (current: {daily_volume}) to improve conversion.",
        budget_tokens=30_000,
        blocked_by=["weekly_analysis"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "client_name": client_name,
        "campaign_name": campaign_name,
        "target_icp": target_icp,
        "channels": channels,
        "daily_volume": daily_volume,
    }
    return workflow


def create_abm_campaign_workflow(
    client_name: str,
    target_accounts: list[str],
    campaign_budget_usd: float = 5000,
) -> WorkflowDefinition:
    """Account-based marketing campaign workflow — orchestrate a high-touch ABM campaign for a LeadForge client."""
    builder = TaskGraphBuilder(f"ABM Campaign: {client_name} ({len(target_accounts)} accounts)", "project")

    builder.task(
        name="account_research",
        agent="sales-researcher",
        description=f"Deep-dive research on target accounts for {client_name}'s ABM campaign: {', '.join(target_accounts)}. Map org structures, identify key stakeholders, surface pain points, and gather technographic/intent data.",
        budget_tokens=80_000,
    ).task(
        name="personalized_content",
        agent="mkt-content",
        description=f"Create account-personalized content for {client_name}'s ABM campaign. Produce custom landing pages, one-pagers, and email copy for each target account: {', '.join(target_accounts)}.",
        budget_tokens=100_000,
        blocked_by=["account_research"],
    ).task(
        name="multi_channel_plan",
        agent="mkt-demandgen",
        description=f"Design multi-channel engagement plan for {client_name}'s ABM campaign. Coordinate LinkedIn ads, direct mail, email, and retargeting across target accounts: {', '.join(target_accounts)}. Budget: ${campaign_budget_usd}.",
        budget_tokens=50_000,
        blocked_by=["account_research"],
    ).task(
        name="budget_approval",
        agent="exec-cfo",
        description=f"Approve campaign budget of ${campaign_budget_usd} for {client_name}'s ABM campaign targeting {len(target_accounts)} accounts.",
        budget_tokens=15_000,
    ).task(
        name="execute_abm",
        agent="sales-sdr",
        description=f"Execute ABM outreach for {client_name}. Deploy personalized content and multi-channel engagement plan across target accounts: {', '.join(target_accounts)}.",
        budget_tokens=60_000,
        blocked_by=["personalized_content", "multi_channel_plan", "budget_approval"],
    ).task(
        name="track_engagement",
        agent="mkt-analytics",
        description=f"Track account-level engagement for {client_name}'s ABM campaign. Monitor content interactions, ad engagement, email responses, and meeting requests across target accounts.",
        budget_tokens=40_000,
        blocked_by=["execute_abm"],
    ).task(
        name="pipeline_report",
        agent="sales-ops",
        description=f"Generate pipeline report for {client_name}'s ABM campaign. Summarize account engagement scores, pipeline created, opportunities advanced, and ROI against ${campaign_budget_usd} budget.",
        budget_tokens=30_000,
        blocked_by=["track_engagement"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "client_name": client_name,
        "target_accounts": target_accounts,
        "campaign_budget_usd": campaign_budget_usd,
    }
    return workflow


def create_client_onboarding_workflow(
    client_name: str,
    client_contact_email: str,
    retainer_amount_usd: float = 5000,
    services: list[str] | None = None,
) -> WorkflowDefinition:
    """Client onboarding workflow — bring a new LeadForge client from signed contract to launch readiness."""
    svc_list = services or ["outbound", "lead_qualification", "nurture"]
    builder = TaskGraphBuilder(f"Client Onboarding: {client_name}", "operational")

    builder.task(
        name="contract_review",
        agent="legal-lead",
        description=f"Final review of signed service agreement for {client_name} ({client_contact_email}). Verify retainer of ${retainer_amount_usd}, SLA terms, data-handling clauses, and scope of services: {', '.join(svc_list)}.",
        priority=TaskPriority.HIGH,
        budget_tokens=40_000,
    ).task(
        name="billing_setup",
        agent="fin-ar",
        description=f"Set up billing for {client_name}. Configure recurring invoice for ${retainer_amount_usd}/month, payment terms, and usage-based overage tracking. Contact: {client_contact_email}.",
        budget_tokens=20_000,
        blocked_by=["contract_review"],
    ).task(
        name="kickoff_prep",
        agent="client-success",
        description=f"Prepare kickoff for {client_name}. Schedule onboarding call with {client_contact_email}, create shared workspace, and assemble welcome packet covering services: {', '.join(svc_list)}.",
        budget_tokens=25_000,
        blocked_by=["contract_review"],
    ).task(
        name="icp_workshop",
        agent="sales-lead",
        description=f"Conduct ICP workshop for {client_name}. Define ideal customer profile, target personas, verticals, firmographic criteria, and exclusion lists to guide lead generation.",
        budget_tokens=50_000,
        blocked_by=["kickoff_prep"],
    ).task(
        name="crm_setup",
        agent="sales-ops",
        description=f"Set up CRM workspace for {client_name}. Configure lead stages, scoring rules, integration with {client_name}'s systems, and reporting dashboards. Services: {', '.join(svc_list)}.",
        budget_tokens=30_000,
        blocked_by=["kickoff_prep"],
    ).task(
        name="template_creation",
        agent="mkt-content",
        description=f"Create outreach and content templates for {client_name}. Develop email sequences, LinkedIn messages, and collateral aligned with {client_name}'s brand voice and ICP.",
        budget_tokens=60_000,
        blocked_by=["icp_workshop"],
    ).task(
        name="compliance_setup",
        agent="legal-compliance",
        description=f"Set up compliance framework for {client_name}. Configure opt-out handling, suppression lists, consent tracking, and regulatory guardrails for outreach in {client_name}'s target markets.",
        budget_tokens=30_000,
        blocked_by=["icp_workshop"],
    ).task(
        name="launch_readiness",
        agent="sales-lead",
        description=f"Final launch readiness review for {client_name}. Verify templates, compliance setup, and CRM configuration are complete. Approve go-live for services: {', '.join(svc_list)} at ${retainer_amount_usd}/month retainer.",
        budget_tokens=20_000,
        blocked_by=["template_creation", "compliance_setup", "crm_setup"],
    )

    workflow = builder.build()
    workflow.metadata = {
        "client_name": client_name,
        "client_contact_email": client_contact_email,
        "retainer_amount_usd": retainer_amount_usd,
        "services": svc_list,
    }
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
