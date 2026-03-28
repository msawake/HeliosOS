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

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

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


@runtime_checkable
class ApprovalStore(Protocol):
    """Protocol for pluggable HITL approval storage backends."""

    def save(self, request: ApprovalRequest) -> None: ...

    def get(self, request_id: str) -> ApprovalRequest | None: ...

    def list_pending(self, category: str | None = None) -> list[ApprovalRequest]: ...

    def update_status(
        self,
        request_id: str,
        status: ApprovalStatus,
        decision_by: str | None = None,
        decision_reason: str | None = None,
    ) -> bool: ...

    def list_expired_pending(self) -> list[ApprovalRequest]: ...


class InMemoryApprovalStore:
    """Default in-memory store. Drop-in for all existing tests and demos."""

    def __init__(self):
        self._requests: dict[str, ApprovalRequest] = {}

    def save(self, request: ApprovalRequest) -> None:
        self._requests[request.id] = request

    def get(self, request_id: str) -> ApprovalRequest | None:
        return self._requests.get(request_id)

    def list_pending(self, category: str | None = None) -> list[ApprovalRequest]:
        results = []
        for req in self._requests.values():
            if req.status != ApprovalStatus.PENDING:
                continue
            if category and req.category != category:
                continue
            results.append(req)
        return sorted(results, key=lambda r: r.timestamp)

    def update_status(
        self,
        request_id: str,
        status: ApprovalStatus,
        decision_by: str | None = None,
        decision_reason: str | None = None,
    ) -> bool:
        req = self._requests.get(request_id)
        if not req or req.status != ApprovalStatus.PENDING:
            return False
        req.status = status
        req.decision_by = decision_by
        req.decision_at = datetime.now(timezone.utc).isoformat()
        req.decision_reason = decision_reason
        return True

    def list_expired_pending(self) -> list[ApprovalRequest]:
        now = datetime.now(timezone.utc)
        results = []
        for req in self._requests.values():
            if req.status != ApprovalStatus.PENDING:
                continue
            if req.deadline and datetime.fromisoformat(req.deadline) <= now:
                results.append(req)
        return results


class PostgresApprovalStore:
    """PostgreSQL-backed store. Uses the MCP postgres server for queries."""

    def __init__(self, db_client, company_id: str = "leadforge"):
        self._db = db_client
        self._company_id = company_id

    def save(self, request: ApprovalRequest) -> None:
        self._db.execute(
            "INSERT INTO hitl_approvals "
            "(id, company_id, created_at, requesting_agent, department, category, "
            "title, description, risk_assessment, sla_hours, deadline, status, context) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                request.id, self._company_id, request.timestamp,
                request.requesting_agent, request.department, request.category,
                request.title, request.description, request.risk_assessment,
                request.sla_hours, request.deadline, request.status.value,
                json.dumps(request.context),
            ),
        )

    def get(self, request_id: str) -> ApprovalRequest | None:
        rows = self._db.execute(
            "SELECT * FROM hitl_approvals WHERE id = %s AND company_id = %s",
            (request_id, self._company_id),
        )
        if not rows:
            return None
        return self._row_to_request(rows[0])

    def list_pending(self, category: str | None = None) -> list[ApprovalRequest]:
        if category:
            rows = self._db.execute(
                "SELECT * FROM hitl_approvals WHERE company_id = %s AND status = 'pending' "
                "AND category = %s ORDER BY created_at",
                (self._company_id, category),
            )
        else:
            rows = self._db.execute(
                "SELECT * FROM hitl_approvals WHERE company_id = %s AND status = 'pending' "
                "ORDER BY created_at",
                (self._company_id,),
            )
        return [self._row_to_request(r) for r in rows]

    def update_status(
        self,
        request_id: str,
        status: ApprovalStatus,
        decision_by: str | None = None,
        decision_reason: str | None = None,
    ) -> bool:
        affected = self._db.execute(
            "UPDATE hitl_approvals SET status = %s, decision_by = %s, "
            "decision_at = %s, decision_reason = %s "
            "WHERE id = %s AND company_id = %s AND status = 'pending'",
            (
                status.value, decision_by,
                datetime.now(timezone.utc).isoformat(), decision_reason,
                request_id, self._company_id,
            ),
        )
        return bool(affected)

    def list_expired_pending(self) -> list[ApprovalRequest]:
        rows = self._db.execute(
            "SELECT * FROM hitl_approvals WHERE company_id = %s "
            "AND status = 'pending' AND deadline <= %s ORDER BY deadline",
            (self._company_id, datetime.now(timezone.utc).isoformat()),
        )
        return [self._row_to_request(r) for r in rows]

    @staticmethod
    def _row_to_request(row: dict) -> ApprovalRequest:
        return ApprovalRequest(
            id=row["id"],
            timestamp=str(row.get("created_at", "")),
            requesting_agent=row.get("requesting_agent", ""),
            department=row.get("department", ""),
            category=row.get("category", ""),
            title=row.get("title", ""),
            description=row.get("description", ""),
            risk_assessment=row.get("risk_assessment", "low"),
            sla_hours=float(row.get("sla_hours", 24.0)),
            deadline=str(row.get("deadline", "")),
            status=ApprovalStatus(row.get("status", "pending")),
            decision_by=row.get("decision_by"),
            decision_at=str(row.get("decision_at", "")) if row.get("decision_at") else None,
            decision_reason=row.get("decision_reason"),
            context=row.get("context") or {},
        )


class HITLGateway:
    """
    Human-in-the-loop approval gateway.
    Manages approval requests and their lifecycle.
    Supports pluggable storage backends (in-memory default, PostgreSQL for production).
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

    def __init__(self, store=None, config: dict | None = None, company_id: str = "leadforge"):
        self._store: InMemoryApprovalStore | PostgresApprovalStore = store or InMemoryApprovalStore()
        self._waiters: dict[str, asyncio.Event] = {}
        self._company_id = company_id
        self._load_sla_overrides(config or {})

    def _load_sla_overrides(self, config: dict) -> None:
        """Merge company-specific SLA overrides from config."""
        hitl_config = config.get("hitl", {})
        sla_overrides = hitl_config.get("sla", {})
        for category, hours in sla_overrides.items():
            self.DEFAULT_SLA[category] = float(hours)

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
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(hours=sla)

        req = ApprovalRequest(
            requesting_agent=requesting_agent,
            department=department,
            category=category,
            title=title,
            description=description,
            risk_assessment=risk_assessment,
            sla_hours=sla,
            deadline=deadline.isoformat(),
            context=context or {},
        )
        self._store.save(req)
        self._waiters[req.id] = asyncio.Event()
        logger.info(
            "HITL REQUEST | %s | %s | %s | SLA: %sh | Deadline: %s",
            req.id[:8], category, title, sla, deadline.isoformat(),
        )
        return req.id

    def get_pending(self, category: str | None = None) -> list[dict]:
        """Get all pending approval requests."""
        pending = self._store.list_pending(category)
        return [
            {
                "id": req.id,
                "timestamp": req.timestamp,
                "agent": req.requesting_agent,
                "department": req.department,
                "category": req.category,
                "title": req.title,
                "description": req.description,
                "risk": req.risk_assessment,
                "sla_hours": req.sla_hours,
                "deadline": req.deadline,
                "status": req.status.value,
            }
            for req in pending
        ]

    def approve(self, request_id: str, approved_by: str, reason: str = "") -> bool:
        success = self._store.update_status(
            request_id, ApprovalStatus.APPROVED,
            decision_by=approved_by, decision_reason=reason,
        )
        if success:
            logger.info("HITL APPROVED | %s | by %s", request_id[:8], approved_by)
            self._signal_waiter(request_id)
        return success

    def reject(self, request_id: str, rejected_by: str, reason: str = "") -> bool:
        success = self._store.update_status(
            request_id, ApprovalStatus.REJECTED,
            decision_by=rejected_by, decision_reason=reason,
        )
        if success:
            logger.info("HITL REJECTED | %s | by %s | %s", request_id[:8], rejected_by, reason)
            self._signal_waiter(request_id)
        return success

    def expire(self, request_id: str) -> bool:
        """Mark a pending request as expired (SLA breached). Auto-deny."""
        success = self._store.update_status(
            request_id, ApprovalStatus.EXPIRED,
            decision_by="system", decision_reason="SLA expired — auto-denied",
        )
        if success:
            logger.warning("HITL EXPIRED | %s | SLA breached", request_id[:8])
            self._signal_waiter(request_id)
        return success

    def get_expired_pending(self) -> list[dict]:
        """Return pending requests that have passed their deadline."""
        expired = self._store.list_expired_pending()
        return [
            {
                "id": req.id,
                "agent": req.requesting_agent,
                "department": req.department,
                "category": req.category,
                "title": req.title,
                "sla_hours": req.sla_hours,
                "deadline": req.deadline,
            }
            for req in expired
        ]

    def check_status(self, request_id: str) -> dict | None:
        req = self._store.get(request_id)
        if not req:
            return None
        return {
            "id": req.id,
            "status": req.status.value,
            "title": req.title,
            "category": req.category,
            "sla_hours": req.sla_hours,
            "deadline": req.deadline,
            "context": req.context,
            "decision_by": req.decision_by,
            "decision_at": req.decision_at,
            "decision_reason": req.decision_reason,
        }

    async def wait_for_decision(self, request_id: str, timeout: float | None = None) -> dict | None:
        """Block until the approval is decided or timeout elapses.

        Returns the status dict on decision, or None on timeout.
        """
        event = self._waiters.get(request_id)
        if not event:
            # Already decided or unknown — return current status
            return self.check_status(request_id)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return self.check_status(request_id)

    def _signal_waiter(self, request_id: str) -> None:
        event = self._waiters.pop(request_id, None)
        if event:
            event.set()


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

    When db_client is provided and connected, uses PostgreSQL-backed subsystems.
    Otherwise falls back to in-memory (for tests and development).
    """

    def __init__(self, config: dict | None = None, company_id: str = "leadforge", db_client=None):
        use_postgres = db_client is not None and getattr(db_client, "is_connected", False)

        if use_postgres:
            from src.mcp.persistence import (
                PostgresEventBus,
                PostgresKnowledgeBase,
                PostgresMetricsStore,
            )
            self.event_bus = PostgresEventBus(db_client, company_id)
            self.knowledge = PostgresKnowledgeBase(db_client, company_id)
            self.metrics = PostgresMetricsStore(db_client, company_id)
            store = PostgresApprovalStore(db_client, company_id)
            logger.info("CompanySystem: using PostgreSQL persistence")
        else:
            self.event_bus = EventBus()
            self.knowledge = KnowledgeBase()
            self.metrics = MetricsStore()
            store = None

        self.hitl = HITLGateway(store=store, config=config, company_id=company_id)

    def seed_knowledge_base(self, company_id: str = "leadforge"):
        """Seed the knowledge base with company-specific policies.

        Delegates to the company's knowledge module. For backward compatibility,
        defaults to LeadForge AI.
        """
        import importlib
        knowledge_mod = importlib.import_module(f"src.companies.{company_id}.knowledge")
        knowledge_mod.seed_knowledge_base(self.knowledge)

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
