"""Tests for the production HITL system: storage, SLA, async waiting, hook bridge."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.mcp.custom_tools import (
    ApprovalRequest,
    ApprovalStatus,
    CompanySystem,
    HITLGateway,
    InMemoryApprovalStore,
)
from src.core.hooks import (
    AgentContext,
    AuthChecker,
    ComplianceChecker,
    HookChain,
    HookDecision,
    SlackNotifier,
    create_hook_chain,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_context(**overrides) -> AgentContext:
    defaults = dict(
        agent_id="test-agent",
        agent_type="doer",
        department="sales",
        tier=3,
        session_id="sess-1",
        allowed_tools=["Bash", "Read", "Agent"],
        budget_tokens=10_000,
        model="claude-sonnet-4-5-20250514",
    )
    defaults.update(overrides)
    return AgentContext(**defaults)


def _make_request(**overrides) -> ApprovalRequest:
    defaults = dict(
        requesting_agent="test-agent",
        department="sales",
        category="financial",
        title="Test approval",
        description="A test",
        sla_hours=24.0,
        deadline=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
    )
    defaults.update(overrides)
    return ApprovalRequest(**defaults)


# ── InMemoryApprovalStore ────────────────────────────────────────────────


class TestInMemoryStore:
    def test_save_and_get(self):
        store = InMemoryApprovalStore()
        req = _make_request()
        store.save(req)
        assert store.get(req.id) is req

    def test_get_missing_returns_none(self):
        store = InMemoryApprovalStore()
        assert store.get("nonexistent") is None

    def test_list_pending(self):
        store = InMemoryApprovalStore()
        r1 = _make_request(title="A")
        r2 = _make_request(title="B")
        store.save(r1)
        store.save(r2)
        pending = store.list_pending()
        assert len(pending) == 2

    def test_list_pending_filters_by_category(self):
        store = InMemoryApprovalStore()
        r1 = _make_request(category="financial")
        r2 = _make_request(category="content")
        store.save(r1)
        store.save(r2)
        assert len(store.list_pending(category="financial")) == 1
        assert len(store.list_pending(category="content")) == 1

    def test_update_status(self):
        store = InMemoryApprovalStore()
        req = _make_request()
        store.save(req)
        assert store.update_status(req.id, ApprovalStatus.APPROVED, "human", "ok")
        assert req.status == ApprovalStatus.APPROVED
        assert req.decision_by == "human"

    def test_update_status_only_pending(self):
        store = InMemoryApprovalStore()
        req = _make_request()
        req.status = ApprovalStatus.APPROVED
        store.save(req)
        assert not store.update_status(req.id, ApprovalStatus.REJECTED)

    def test_list_expired_pending(self):
        store = InMemoryApprovalStore()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        r1 = _make_request(deadline=past, title="expired")
        r2 = _make_request(deadline=future, title="active")
        store.save(r1)
        store.save(r2)
        expired = store.list_expired_pending()
        assert len(expired) == 1
        assert expired[0].title == "expired"


# ── HITLGateway SLA ──────────────────────────────────────────────────────


class TestHITLGatewaySLA:
    def test_default_sla_applied(self):
        gw = HITLGateway()
        req_id = gw.request_approval("agent", "sales", "financial", "test", "desc")
        status = gw.check_status(req_id)
        assert status["sla_hours"] == 24.0

    def test_config_sla_override(self):
        config = {"hitl": {"sla": {"financial": 2.0}}}
        gw = HITLGateway(config=config)
        req_id = gw.request_approval("agent", "sales", "financial", "test", "desc")
        status = gw.check_status(req_id)
        assert status["sla_hours"] == 2.0

    def test_deadline_computed(self):
        gw = HITLGateway()
        before = datetime.now(timezone.utc)
        req_id = gw.request_approval("agent", "sales", "content", "test", "desc")
        after = datetime.now(timezone.utc)
        status = gw.check_status(req_id)
        deadline = datetime.fromisoformat(status["deadline"])
        # Content SLA is 4h
        assert deadline >= before + timedelta(hours=4)
        assert deadline <= after + timedelta(hours=4, seconds=1)

    def test_expire_marks_expired(self):
        gw = HITLGateway()
        req_id = gw.request_approval("agent", "sales", "financial", "test", "desc")
        assert gw.expire(req_id)
        status = gw.check_status(req_id)
        assert status["status"] == "expired"
        assert status["decision_by"] == "system"

    def test_expire_idempotent(self):
        gw = HITLGateway()
        req_id = gw.request_approval("agent", "sales", "financial", "test", "desc")
        assert gw.expire(req_id)
        assert not gw.expire(req_id)  # already expired

    def test_get_expired_pending(self):
        gw = HITLGateway()
        # Create with 0h SLA so deadline is in the past
        config = {"hitl": {"sla": {"financial": 0.0}}}
        gw2 = HITLGateway(config=config)
        gw2.request_approval("agent", "sales", "financial", "expired", "desc")
        expired = gw2.get_expired_pending()
        assert len(expired) == 1
        assert expired[0]["title"] == "expired"

    def test_check_status_includes_context(self):
        gw = HITLGateway()
        req_id = gw.request_approval(
            "agent", "sales", "financial", "test", "desc",
            context={"amount": 5000},
        )
        status = gw.check_status(req_id)
        assert status["context"] == {"amount": 5000}
        assert "deadline" in status
        assert "sla_hours" in status


# ── Async Waiting ────────────────────────────────────────────────────────


class TestHITLAsyncWaiting:
    @pytest.mark.asyncio
    async def test_wait_resolves_on_approve(self):
        gw = HITLGateway()
        req_id = gw.request_approval("agent", "sales", "financial", "test", "desc")

        async def approve_later():
            await asyncio.sleep(0.05)
            gw.approve(req_id, "human")

        asyncio.create_task(approve_later())
        result = await gw.wait_for_decision(req_id, timeout=2.0)
        assert result is not None
        assert result["status"] == "approved"

    @pytest.mark.asyncio
    async def test_wait_resolves_on_reject(self):
        gw = HITLGateway()
        req_id = gw.request_approval("agent", "sales", "financial", "test", "desc")

        async def reject_later():
            await asyncio.sleep(0.05)
            gw.reject(req_id, "human", "nope")

        asyncio.create_task(reject_later())
        result = await gw.wait_for_decision(req_id, timeout=2.0)
        assert result is not None
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_wait_timeout_returns_none(self):
        gw = HITLGateway()
        req_id = gw.request_approval("agent", "sales", "financial", "test", "desc")
        result = await gw.wait_for_decision(req_id, timeout=0.05)
        assert result is None


# ── Hook-to-HITL Bridge ─────────────────────────────────────────────────


class TestHookHITLBridge:
    def test_ask_human_creates_approval(self):
        gw = HITLGateway()
        chain = HookChain(hitl_gateway=gw)
        ctx = _make_context(
            allowed_tools=["mcp__google-workspace__transfer_drive_ownership"],
        )
        result = chain.pre_tool_use(
            ctx, "mcp__google-workspace__transfer_drive_ownership",
        )
        assert result.decision == HookDecision.ASK_HUMAN
        assert "approval_request_id" in result.metadata
        # Verify approval was created in gateway
        req_id = result.metadata["approval_request_id"]
        status = gw.check_status(req_id)
        assert status is not None
        assert status["status"] == "pending"

    def test_ask_human_without_gateway_blocks(self):
        chain = HookChain(hitl_gateway=None)
        ctx = _make_context(
            allowed_tools=["mcp__google-workspace__transfer_drive_ownership"],
        )
        result = chain.pre_tool_use(
            ctx, "mcp__google-workspace__transfer_drive_ownership",
        )
        assert result.decision == HookDecision.BLOCK
        assert "no HITL gateway" in result.reason

    def test_ask_human_sends_slack_notification(self):
        gw = HITLGateway()
        chain = HookChain(hitl_gateway=gw)
        ctx = _make_context(
            allowed_tools=["mcp__google-workspace__transfer_drive_ownership"],
        )
        chain.pre_tool_use(
            ctx, "mcp__google-workspace__transfer_drive_ownership",
        )
        notifications = chain.slack.get_pending()
        approval_notifs = [n for n in notifications if n["category"] == "approval"]
        assert len(approval_notifs) >= 1
        assert "transfer_drive_ownership" in approval_notifs[0]["title"]

    def test_mass_email_creates_approval(self):
        gw = HITLGateway()
        chain = HookChain(hitl_gateway=gw)
        ctx = _make_context(
            allowed_tools=["mcp__google-workspace__send_gmail_message"],
        )
        # 51 recipients triggers ASK_HUMAN from compliance checker
        recipients = ", ".join([f"user{i}@test.com" for i in range(51)])
        result = chain.pre_tool_use(
            ctx,
            "mcp__google-workspace__send_gmail_message",
            {"to": recipients, "subject": "Hello", "body": "Test"},
        )
        assert result.decision == HookDecision.ASK_HUMAN
        assert "approval_request_id" in result.metadata

    def test_mass_email_without_gateway_blocks(self):
        chain = HookChain(hitl_gateway=None)
        ctx = _make_context(
            allowed_tools=["mcp__google-workspace__send_gmail_message"],
        )
        recipients = ", ".join([f"user{i}@test.com" for i in range(51)])
        result = chain.pre_tool_use(
            ctx,
            "mcp__google-workspace__send_gmail_message",
            {"to": recipients, "subject": "Hello", "body": "Test"},
        )
        assert result.decision == HookDecision.BLOCK


# ── CompanySystem backward compat ────────────────────────────────────────


class TestCompanySystemCompat:
    def test_no_args_still_works(self):
        system = CompanySystem()
        assert system.hitl is not None
        assert system.event_bus is not None

    def test_with_config(self):
        config = {"hitl": {"sla": {"financial": 1.0}}}
        system = CompanySystem(config=config, company_id="leadforge")
        req_id = system.hitl.request_approval("a", "sales", "financial", "t", "d")
        status = system.hitl.check_status(req_id)
        assert status["sla_hours"] == 1.0


# ── create_hook_chain backward compat ────────────────────────────────────


class TestCreateHookChainCompat:
    def test_no_args_still_works(self):
        chain = create_hook_chain()
        assert chain._hitl is None

    def test_with_hitl_gateway(self):
        gw = HITLGateway()
        chain = create_hook_chain(hitl_gateway=gw)
        assert chain._hitl is gw
