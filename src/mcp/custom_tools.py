"""
Custom MCP tool definitions for company-specific operations.

These tools extend the standard MCP ecosystem with business logic specific
to the AI-operated company. They are registered as in-process MCP servers
via the Agent SDK's `create_sdk_mcp_server()`.

Tool categories:
1. Event Bus     - Cross-department communication
2. HITL Gateway  - Human-in-the-loop approval requests
3. Knowledge Base - Company knowledge and decision precedents
4. Agent Registry - Agent discovery and health
5. Metrics       - Business metrics and KPI tracking
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Event Bus Tools
# ---------------------------------------------------------------------------

class EventType(Enum):
    REQUEST = "REQUEST"
    RESPONSE = "RESPONSE"
    NOTIFICATION = "NOTIFICATION"
    ESCALATION = "ESCALATION"


class EventPriority(Enum):
    P0_CRITICAL = "P0_CRITICAL"
    P1_HIGH = "P1_HIGH"
    P2_MEDIUM = "P2_MEDIUM"
    P3_LOW = "P3_LOW"


class EventStatus(Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


@dataclass
class Event:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_agent: str = ""
    source_department: str = ""
    target_department: str = ""
    event_type: EventType = EventType.NOTIFICATION
    category: str = ""
    payload: dict = field(default_factory=dict)
    status: EventStatus = EventStatus.PENDING
    priority: EventPriority = EventPriority.P2_MEDIUM
    parent_event_id: str | None = None
    resolved_at: str | None = None
    resolution: dict | None = None


class EventBus:
    """
    In-memory event bus for cross-department communication.
    In production, backed by PostgreSQL `events` table.
    """

    def __init__(self):
        self._events: dict[str, Event] = {}

    def publish(
        self,
        source_agent: str,
        source_department: str,
        target_department: str,
        event_type: str,
        category: str,
        payload: dict,
        priority: str = "P2_MEDIUM",
        parent_event_id: str | None = None,
    ) -> str:
        """Publish an event to the bus."""
        event = Event(
            source_agent=source_agent,
            source_department=source_department,
            target_department=target_department,
            event_type=EventType(event_type),
            category=category,
            payload=payload,
            priority=EventPriority(priority),
            parent_event_id=parent_event_id,
        )
        self._events[event.id] = event
        logger.info(
            "EVENT | %s | %s -> %s | %s | %s",
            event.id[:8], source_department, target_department, category, priority,
        )
        return event.id

    def query(
        self,
        target_department: str | None = None,
        status: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query events with filters."""
        results = []
        for event in self._events.values():
            if target_department and event.target_department != target_department:
                continue
            if status and event.status.value != status:
                continue
            if category and event.category != category:
                continue
            if priority and event.priority.value != priority:
                continue
            results.append({
                "id": event.id,
                "timestamp": event.timestamp,
                "source_agent": event.source_agent,
                "source_department": event.source_department,
                "target_department": event.target_department,
                "event_type": event.event_type.value,
                "category": event.category,
                "payload": event.payload,
                "status": event.status.value,
                "priority": event.priority.value,
                "parent_event_id": event.parent_event_id,
            })
            if len(results) >= limit:
                break
        return sorted(results, key=lambda e: e["timestamp"], reverse=True)

    def resolve(
        self,
        event_id: str,
        resolution: dict,
    ) -> bool:
        """Resolve an event."""
        event = self._events.get(event_id)
        if not event:
            return False
        event.status = EventStatus.RESOLVED
        event.resolved_at = datetime.now(timezone.utc).isoformat()
        event.resolution = resolution
        return True

    def claim(self, event_id: str, agent_id: str) -> bool:
        """Claim an event for processing."""
        event = self._events.get(event_id)
        if not event or event.status != EventStatus.PENDING:
            return False
        event.status = EventStatus.IN_PROGRESS
        event.payload["claimed_by"] = agent_id
        event.payload["claimed_at"] = datetime.now(timezone.utc).isoformat()
        return True


# ---------------------------------------------------------------------------
# 2. HITL Gateway Tools
# ---------------------------------------------------------------------------

class ApprovalStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class ApprovalRequest:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    requesting_agent: str = ""
    department: str = ""
    category: str = ""  # financial | content | contract | hiring | security
    title: str = ""
    description: str = ""
    risk_assessment: str = "low"
    sla_hours: float = 24.0
    deadline: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision_by: str | None = None
    decision_at: str | None = None
    decision_reason: str | None = None
    context: dict = field(default_factory=dict)


class HITLGateway:
    """
    Human-in-the-loop approval gateway.
    Manages approval requests and their lifecycle.
    """

    # Default SLA by category
    DEFAULT_SLA = {
        "financial": 24.0,
        "content": 4.0,
        "contract": 48.0,
        "client_agreement": 48.0,
        "outreach_compliance": 4.0,
        "ad_spend": 12.0,
        "data_deletion": 24.0,
    }

    def __init__(self):
        self._requests: dict[str, ApprovalRequest] = {}

    def request_approval(
        self,
        requesting_agent: str,
        department: str,
        category: str,
        title: str,
        description: str,
        risk_assessment: str = "low",
        context: dict | None = None,
    ) -> str:
        """Submit a new approval request."""
        sla = self.DEFAULT_SLA.get(category, 24.0)

        req = ApprovalRequest(
            requesting_agent=requesting_agent,
            department=department,
            category=category,
            title=title,
            description=description,
            risk_assessment=risk_assessment,
            sla_hours=sla,
            context=context or {},
        )
        self._requests[req.id] = req
        logger.info(
            "HITL REQUEST | %s | %s | %s | SLA: %sh",
            req.id[:8], category, title, sla,
        )
        return req.id

    def get_pending(self, category: str | None = None) -> list[dict]:
        """Get all pending approval requests."""
        results = []
        for req in self._requests.values():
            if req.status != ApprovalStatus.PENDING:
                continue
            if category and req.category != category:
                continue
            results.append({
                "id": req.id,
                "timestamp": req.timestamp,
                "agent": req.requesting_agent,
                "department": req.department,
                "category": req.category,
                "title": req.title,
                "description": req.description,
                "risk": req.risk_assessment,
                "sla_hours": req.sla_hours,
                "status": req.status.value,
            })
        return sorted(results, key=lambda r: r["timestamp"])

    def approve(self, request_id: str, approved_by: str, reason: str = "") -> bool:
        req = self._requests.get(request_id)
        if not req or req.status != ApprovalStatus.PENDING:
            return False
        req.status = ApprovalStatus.APPROVED
        req.decision_by = approved_by
        req.decision_at = datetime.now(timezone.utc).isoformat()
        req.decision_reason = reason
        logger.info("HITL APPROVED | %s | by %s", request_id[:8], approved_by)
        return True

    def reject(self, request_id: str, rejected_by: str, reason: str = "") -> bool:
        req = self._requests.get(request_id)
        if not req or req.status != ApprovalStatus.PENDING:
            return False
        req.status = ApprovalStatus.REJECTED
        req.decision_by = rejected_by
        req.decision_at = datetime.now(timezone.utc).isoformat()
        req.decision_reason = reason
        logger.info("HITL REJECTED | %s | by %s | %s", request_id[:8], rejected_by, reason)
        return True

    def check_status(self, request_id: str) -> dict | None:
        req = self._requests.get(request_id)
        if not req:
            return None
        return {
            "id": req.id,
            "status": req.status.value,
            "title": req.title,
            "category": req.category,
            "decision_by": req.decision_by,
            "decision_at": req.decision_at,
            "decision_reason": req.decision_reason,
        }


# ---------------------------------------------------------------------------
# 3. Knowledge Base Tools
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeEntry:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    category: str = ""  # policy | procedure | decision | faq | technical
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    created_by: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""
    department: str = ""
    references: list[str] = field(default_factory=list)


class KnowledgeBase:
    """
    Company knowledge base for policies, procedures, decisions, and FAQs.
    In production, backed by PostgreSQL + Pinecone for semantic search.
    """

    def __init__(self):
        self._entries: dict[str, KnowledgeEntry] = {}

    def add(
        self,
        category: str,
        title: str,
        content: str,
        tags: list[str],
        created_by: str,
        department: str = "",
    ) -> str:
        entry = KnowledgeEntry(
            category=category,
            title=title,
            content=content,
            tags=tags,
            created_by=created_by,
            department=department,
        )
        self._entries[entry.id] = entry
        return entry.id

    def search(
        self,
        query: str,
        category: str | None = None,
        department: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search knowledge base. In production, uses vector similarity."""
        results = []
        query_lower = query.lower()
        for entry in self._entries.values():
            if category and entry.category != category:
                continue
            if department and entry.department != department:
                continue
            # Simple keyword matching (vector search in production)
            score = 0
            if query_lower in entry.title.lower():
                score += 10
            if query_lower in entry.content.lower():
                score += 5
            for tag in entry.tags:
                if query_lower in tag.lower():
                    score += 3
            if score > 0:
                results.append({
                    "id": entry.id,
                    "category": entry.category,
                    "title": entry.title,
                    "content": entry.content[:500],
                    "tags": entry.tags,
                    "department": entry.department,
                    "score": score,
                })
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    def get(self, entry_id: str) -> dict | None:
        entry = self._entries.get(entry_id)
        if not entry:
            return None
        return {
            "id": entry.id,
            "category": entry.category,
            "title": entry.title,
            "content": entry.content,
            "tags": entry.tags,
            "created_by": entry.created_by,
            "created_at": entry.created_at,
            "department": entry.department,
        }

    def add_decision_precedent(
        self,
        title: str,
        decision: str,
        reasoning: str,
        made_by: str,
        department: str,
        outcome: str = "",
    ) -> str:
        """Record a decision for future reference."""
        content = (
            f"Decision: {decision}\n\n"
            f"Reasoning: {reasoning}\n\n"
            f"Outcome: {outcome}"
        )
        return self.add(
            category="decision",
            title=title,
            content=content,
            tags=["decision", department],
            created_by=made_by,
            department=department,
        )


# ---------------------------------------------------------------------------
# 4. Metrics Tools
# ---------------------------------------------------------------------------

@dataclass
class MetricPoint:
    name: str
    value: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    department: str = ""
    tags: dict[str, str] = field(default_factory=dict)


class MetricsStore:
    """
    Business metrics and KPI tracking.
    In production, backed by Prometheus/Datadog + PostgreSQL.
    """

    def __init__(self):
        self._metrics: list[MetricPoint] = []
        self._gauges: dict[str, float] = {}

    def record(
        self,
        name: str,
        value: float,
        department: str = "",
        tags: dict[str, str] | None = None,
    ):
        """Record a metric data point."""
        point = MetricPoint(
            name=name,
            value=value,
            department=department,
            tags=tags or {},
        )
        self._metrics.append(point)
        self._gauges[name] = value

    def increment(self, name: str, amount: float = 1.0, department: str = ""):
        """Increment a counter metric."""
        current = self._gauges.get(name, 0.0)
        self.record(name, current + amount, department)

    def get_current(self, name: str) -> float | None:
        return self._gauges.get(name)

    def query(
        self,
        name: str,
        department: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query metric history."""
        results = []
        for point in reversed(self._metrics):
            if point.name != name:
                continue
            if department and point.department != department:
                continue
            results.append({
                "name": point.name,
                "value": point.value,
                "timestamp": point.timestamp,
                "department": point.department,
                "tags": point.tags,
            })
            if len(results) >= limit:
                break
        return results

    def get_dashboard(self) -> dict[str, float]:
        """Get current values of all gauges."""
        return dict(self._gauges)


# ---------------------------------------------------------------------------
# 5. Company System (unified access point)
# ---------------------------------------------------------------------------

class CompanySystem:
    """
    Unified access to all company subsystems.
    This is the single object that the bootstrap process creates
    and passes to all agent invocations.
    """

    def __init__(self):
        self.event_bus = EventBus()
        self.hitl = HITLGateway()
        self.knowledge = KnowledgeBase()
        self.metrics = MetricsStore()

    def seed_knowledge_base(self):
        """Seed the knowledge base with LeadForge AI policies and procedures."""
        self.knowledge.add(
            "procedure", "ICP Definition Framework",
            "Framework for defining Ideal Customer Profiles per client. Includes: industry verticals, "
            "company size (revenue and employee count), technology stack signals, buying triggers "
            "(hiring, funding, expansion), organizational maturity indicators, geographic targeting, "
            "decision-maker titles and roles. Every client engagement starts with ICP workshop.",
            ["sales", "icp", "targeting"], "system"
        )
        self.knowledge.add(
            "procedure", "Lead Scoring Criteria (BANT/MEDDIC)",
            "Lead scoring rubric: Budget (0-25 points: Has budget allocated or process identified?), "
            "Authority (0-25: Is contact a decision maker or has access?), Need (0-25: Expressed pain "
            "point matching solution?), Timeline (0-25: Active buying timeline within 90 days?). "
            "Score 70+: SQL (hand off to client). Score 40-69: MQL (enter nurture sequence). "
            "Score below 40: Archive (revisit quarterly). MEDDIC overlay for enterprise deals >$50K.",
            ["sales", "scoring", "qualification"], "system"
        )
        self.knowledge.add(
            "policy", "Outreach Compliance (CAN-SPAM / GDPR)",
            "CAN-SPAM: Must include physical address, unsubscribe link, honest subject lines, no "
            "misleading headers. Honor opt-outs within 10 business days. GDPR: Legitimate interest "
            "basis for B2B outreach, right to object must be honored within 72 hours, data processing "
            "records required, no outreach to personal email addresses in EU without consent. "
            "Maximum outreach frequency: 3 emails per prospect per week. Opt-out processed within 24h.",
            ["legal", "compliance", "outreach", "email"], "system"
        )
        self.knowledge.add(
            "procedure", "Email Outreach Cadence Rules",
            "Standard outreach sequence: Day 1: Intro email (personalized to prospect pain points). "
            "Day 3: LinkedIn connection request with custom note. Day 5: Follow-up email with value-add "
            "content (case study or whitepaper). Day 8: LinkedIn message. Day 12: Breakup email. "
            "Wait 30 days before re-engaging. Maximum 50 new prospects per SDR per day. All emails "
            "sent between 8am-6pm recipient local time. All sequences use client-approved templates.",
            ["sales", "outreach", "cadence"], "system"
        )
        self.knowledge.add(
            "procedure", "Qualification Criteria",
            "A lead qualifies as SQL when: (1) Confirmed budget or budget process identified, "
            "(2) Spoke with economic buyer or champion with access to buyer, (3) Expressed specific "
            "pain point our client's service addresses, (4) Timeline within 90 days, "
            "(5) No competing engagement with direct competitor. Minimum 3 of 5 criteria met. "
            "All SQLs must have a booked meeting or call scheduled with client sales team.",
            ["sales", "qualification", "sql"], "system"
        )
        self.knowledge.add(
            "policy", "Client SLA Framework",
            "Standard client SLAs by retainer tier: Starter ($3K/month): 50 qualified leads/month, "
            "5 SQLs, weekly email reporting. Growth ($5K/month): 100 qualified leads/month, "
            "10 SQLs, bi-weekly strategy calls, dedicated Slack channel. Enterprise ($10K/month): "
            "200 qualified leads/month, 20 SQLs, dedicated strategist, daily Slack channel, "
            "monthly QBR. Performance bonus: $500 per SQL that converts to opportunity. "
            "Meeting no-show rate must be below 15%.",
            ["operations", "client", "sla"], "system"
        )
        self.knowledge.add(
            "policy", "Financial Approval Thresholds",
            "Up to $1,000: Department lead approval. $1,000-$5,000: CFO approval. "
            "$5,000-$10,000: CEO approval. Over $10,000: Human board approval. "
            "Client refunds >$1,000 require CEO approval. Google Ads spend increases >20% "
            "require CFO approval. Performance bonus payouts auto-approved per SLA terms.",
            ["finance", "approval"], "system"
        )
        self.knowledge.add(
            "policy", "Escalation Protocol",
            "Level 1: Same-department — department lead arbitrates. "
            "Level 2: Cross-department — COO arbitrates (council pattern). "
            "Level 3: Strategic — escalate to human board with structured decision document. "
            "Level 4: Red line — ANY agent can bypass hierarchy for ethical/legal/safety concerns "
            "via ESCALATION_CRITICAL event. Client escalations: churn risk goes directly to "
            "sales-lead and exec-coo simultaneously.",
            ["operations", "escalation"], "system"
        )
        self.knowledge.add(
            "policy", "Data Handling Policy",
            "No PII in agent prompts or logs. Prospect data handled per client data processing "
            "agreements. No cross-client data sharing or list mixing under any circumstances. "
            "Data deletion follows GDPR workflow. All data access logged in audit trail. "
            "Prospect data retained for maximum 12 months after last engagement unless client "
            "requests extension. Suppression lists maintained per client and per jurisdiction.",
            ["legal", "data", "privacy"], "system"
        )
        self.knowledge.add(
            "policy", "Agent Autonomy Levels",
            "Category A (Fully Autonomous): Lead scoring, prospect research, CRM updates, "
            "template-based outreach, pipeline reporting, data enrichment. "
            "Category B (Autonomous + Audit): Outreach emails (10% weekly sample), nurture sequences, "
            "campaign optimization, ad bid adjustments, content creation. "
            "Category C (Pre-Approval Required): Client service agreements (48h SLA), ad spend "
            "changes >$500 (12h), new outreach channels (24h), pricing changes (24h). "
            "Category D (Human-Only): Legal agreements, regulatory filings, strategic pivots, "
            "data breach response, client terminations.",
            ["operations", "autonomy", "governance"], "system"
        )

    def get_system_health(self) -> dict:
        """Get overall system health summary."""
        pending_events = len(self.event_bus.query(status="PENDING"))
        pending_approvals = len(self.hitl.get_pending())
        metrics = self.metrics.get_dashboard()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pending_events": pending_events,
            "pending_approvals": pending_approvals,
            "metrics": metrics,
        }
