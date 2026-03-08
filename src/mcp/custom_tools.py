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
        "hiring": 48.0,
        "security": 4.0,
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
        """Seed the knowledge base with initial company policies."""
        policies = [
            {
                "title": "Financial Approval Thresholds",
                "content": (
                    "Expenditures up to $1,000: Department lead can approve.\n"
                    "Expenditures $1,000-$5,000: CFO approval required.\n"
                    "Expenditures $5,000-$10,000: CEO approval required.\n"
                    "Expenditures over $10,000: Human board approval required."
                ),
                "tags": ["finance", "policy", "approval"],
                "department": "finance",
            },
            {
                "title": "Code Review Policy",
                "content": (
                    "All code changes require at least one reviewer approval.\n"
                    "Security-sensitive changes require eng-security review.\n"
                    "Infrastructure changes require eng-infra review.\n"
                    "No self-approvals. Reviewer must be different from author."
                ),
                "tags": ["engineering", "policy", "code-review"],
                "department": "engineering",
            },
            {
                "title": "Customer Communication Policy",
                "content": (
                    "All external communications must pass compliance check.\n"
                    "No guaranteed outcomes or misleading claims.\n"
                    "Escalate immediately if customer mentions: legal, lawsuit, breach.\n"
                    "Critical incident communications require human review."
                ),
                "tags": ["support", "policy", "communication"],
                "department": "support",
            },
            {
                "title": "Data Handling Policy",
                "content": (
                    "PII must never be logged or stored in plain text.\n"
                    "Data deletion requests follow GDPR right-to-erasure workflow.\n"
                    "Customer data access is logged in audit trail.\n"
                    "No customer data in agent prompts or system messages."
                ),
                "tags": ["security", "policy", "data", "privacy"],
                "department": "legal",
            },
            {
                "title": "Escalation Protocol",
                "content": (
                    "Level 1: Same-department orchestrator arbitrates.\n"
                    "Level 2: COO arbitrates cross-department disagreements.\n"
                    "Level 3: Human board for strategic disagreements.\n"
                    "Level 4: Immediate human escalation for ethics/legal/safety red lines.\n"
                    "Any agent can invoke Level 4 regardless of hierarchy."
                ),
                "tags": ["policy", "escalation", "governance"],
                "department": "operations",
            },
            {
                "title": "Agent Autonomy Levels",
                "content": (
                    "Category A (Fully Autonomous): Ticket routing, code review, task assignment, data analysis.\n"
                    "Category B (Autonomous + Audit): Support responses, sales outreach, bug prioritization.\n"
                    "Category C (Pre-Approval): Financial >$1K, contracts, hiring, security exceptions.\n"
                    "Category D (Human-Only): Legal agreements, regulatory filings, strategic pivots, crisis response."
                ),
                "tags": ["policy", "autonomy", "governance"],
                "department": "operations",
            },
        ]

        for policy in policies:
            self.knowledge.add(
                category="policy",
                title=policy["title"],
                content=policy["content"],
                tags=policy["tags"],
                created_by="system",
                department=policy["department"],
            )

        logger.info("Seeded knowledge base with %d policies", len(policies))

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
