"""Tests for the H2A/A2H protocol — human-agent interaction."""

import pytest

from src.platform.h2a import (
    DashboardChannel,
    H2AGateway,
    HumanAgent,
    HumanRequest,
    InMemoryHumanRequestStore,
    Priority,
    RequestStatus,
    RequestType,
    ResponseType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _gateway() -> H2AGateway:
    gw = H2AGateway()
    gw.register_human(HumanAgent(
        pid="human:jama", name="jama", namespace="engineering",
        role="engineering-lead", channels=["dashboard"],
    ))
    gw.register_human(HumanAgent(
        pid="human:sarah", name="sarah", namespace="sales",
        role="sales-director", channels=["dashboard", "slack"],
    ))
    gw.register_human(HumanAgent(
        pid="human:auto-approver", name="auto-approver", namespace="finance",
        role="finance-lead",
        delegation_rules={
            "auto_approve": {"agents": ["sales-*"], "max_value": 10000},
        },
    ))
    return gw


# ---------------------------------------------------------------------------
# Human registration
# ---------------------------------------------------------------------------

class TestHumanRegistration:
    def test_register_human(self):
        gw = _gateway()
        humans = gw.list_humans()
        assert len(humans) == 3

    def test_resolve_by_namespace_name(self):
        gw = _gateway()
        h = gw.resolve_human("engineering", "jama")
        assert h is not None
        assert h.role == "engineering-lead"

    def test_resolve_missing_human(self):
        gw = _gateway()
        assert gw.resolve_human("engineering", "nobody") is None

    def test_list_by_namespace(self):
        gw = _gateway()
        sales = gw.list_humans(namespace="sales")
        assert len(sales) == 1
        assert sales[0].name == "sarah"

    def test_unregister(self):
        gw = _gateway()
        assert gw.unregister_human("human:jama") is True
        assert gw.resolve_human("engineering", "jama") is None

    def test_discovery_dict(self):
        h = HumanAgent(pid="human:test", name="test", namespace="eng", role="dev")
        d = h.to_discovery_dict()
        assert d["type"] == "human"
        assert d["stack"] == "human"
        assert d["role"] == "dev"


# ---------------------------------------------------------------------------
# A2H: Agent asks human
# ---------------------------------------------------------------------------

class TestAgentAsksHuman:
    async def test_ask_creates_pending_request(self):
        gw = _gateway()
        result = await gw.ask(
            from_agent="agent-123", from_agent_name="research-analyst",
            to_namespace="engineering", to_name="jama",
            question="Should we proceed with the MegaInc deal?",
            response_type="choice",
            options=[{"label": "Yes"}, {"label": "No"}],
            priority="P1_HIGH",
        )
        assert result["success"] is True
        assert result["status"] == "pending"
        assert "request_id" in result
        assert result["auto_responded"] is False

    async def test_ask_unknown_human_fails(self):
        gw = _gateway()
        result = await gw.ask(
            from_agent="agent-123", from_agent_name="test",
            to_namespace="engineering", to_name="nobody",
            question="Hello?",
        )
        assert result["success"] is False
        assert "not found" in result["error"]

    async def test_ask_with_context(self):
        gw = _gateway()
        result = await gw.ask(
            from_agent="agent-123", from_agent_name="sales-pipeline",
            to_namespace="engineering", to_name="jama",
            question="Approve this?",
            context={"deal_value": 50000, "lead_score": 85},
        )
        req = gw.get_request(result["request_id"])
        assert req["context"]["deal_value"] == 50000

    async def test_ask_sets_deadline(self):
        gw = _gateway()
        result = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Quick question?", sla_hours=2.0,
        )
        req = gw.get_request(result["request_id"])
        assert req["deadline"] is not None


# ---------------------------------------------------------------------------
# Human responds
# ---------------------------------------------------------------------------

class TestHumanResponds:
    async def test_respond_to_pending_request(self):
        gw = _gateway()
        ask_result = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Yes or no?", response_type="choice",
            options=[{"label": "Yes"}, {"label": "No"}],
        )
        request_id = ask_result["request_id"]

        resp = gw.respond(request_id, {"choice": "Yes", "note": "Looks good"})
        assert resp["success"] is True

        req = gw.get_request(request_id)
        assert req["status"] == "answered"
        assert req["response"]["choice"] == "Yes"
        assert req["responded_via"] == "dashboard"

    async def test_respond_to_nonexistent_fails(self):
        gw = _gateway()
        resp = gw.respond("nonexistent", {"choice": "Yes"})
        assert resp["success"] is False

    async def test_double_respond_fails(self):
        gw = _gateway()
        ask_result = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Once only?",
        )
        request_id = ask_result["request_id"]

        gw.respond(request_id, {"text": "First"})
        resp2 = gw.respond(request_id, {"text": "Second"})
        assert resp2["success"] is False
        assert "not pending" in resp2["error"]


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class TestNotifications:
    async def test_notify_human(self):
        gw = _gateway()
        result = await gw.notify(
            from_agent="agent-x", from_agent_name="daily-reporter",
            to_namespace="sales", to_name="sarah",
            message="Daily report: 12 leads qualified",
            priority="P3_LOW",
        )
        assert result["success"] is True
        assert result["delivered"] is True

    async def test_notify_unknown_human_fails(self):
        gw = _gateway()
        result = await gw.notify(
            from_agent="a", from_agent_name="b",
            to_namespace="sales", to_name="nobody",
            message="Hello?",
        )
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Auto-delegation rules
# ---------------------------------------------------------------------------

class TestAutoDelegation:
    async def test_auto_approve_matching_rule(self):
        gw = _gateway()
        result = await gw.ask(
            from_agent="agent-sales", from_agent_name="sales-pipeline-agent",
            to_namespace="finance", to_name="auto-approver",
            question="Approve $5K campaign?",
            response_type="approval",
            context={"value": 5000},
        )
        assert result["success"] is True
        assert result["auto_responded"] is True
        assert result["status"] == "answered"

        req = gw.get_request(result["request_id"])
        assert req["response"]["decision"] == "approved"
        assert req["responded_via"] == "auto_delegation"

    async def test_auto_approve_over_limit_stays_pending(self):
        gw = _gateway()
        result = await gw.ask(
            from_agent="agent-sales", from_agent_name="sales-pipeline-agent",
            to_namespace="finance", to_name="auto-approver",
            question="Approve $50K campaign?",
            response_type="approval",
            context={"value": 50000},
        )
        assert result["status"] == "pending"
        assert result["auto_responded"] is False

    async def test_auto_approve_wrong_agent_stays_pending(self):
        gw = _gateway()
        result = await gw.ask(
            from_agent="agent-x", from_agent_name="random-agent",
            to_namespace="finance", to_name="auto-approver",
            question="Approve something?",
            response_type="approval",
            context={"value": 100},
        )
        assert result["status"] == "pending"


# ---------------------------------------------------------------------------
# Pending list
# ---------------------------------------------------------------------------

class TestPendingList:
    async def test_list_pending_for_human(self):
        gw = _gateway()
        await gw.ask(from_agent="a", from_agent_name="b",
                     to_namespace="engineering", to_name="jama", question="Q1")
        await gw.ask(from_agent="a", from_agent_name="b",
                     to_namespace="engineering", to_name="jama", question="Q2")
        await gw.ask(from_agent="a", from_agent_name="b",
                     to_namespace="sales", to_name="sarah", question="Q3")

        jama_pending = gw.list_pending(human_pid="human:jama")
        assert len(jama_pending) == 2

        sarah_pending = gw.list_pending(human_pid="human:sarah")
        assert len(sarah_pending) == 1

        all_pending = gw.list_pending()
        assert len(all_pending) == 3


# ---------------------------------------------------------------------------
# Async wait for response
# ---------------------------------------------------------------------------

class TestAsyncWait:
    async def test_wait_returns_after_response(self):
        gw = _gateway()
        ask_result = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Waiting for you...",
        )
        request_id = ask_result["request_id"]

        # Simulate human responding after a short delay
        import asyncio
        async def _respond_later():
            await asyncio.sleep(0.1)
            gw.respond(request_id, {"text": "Here I am!"})

        asyncio.create_task(_respond_later())
        result = await gw.wait_for_response(request_id, timeout=5.0)

        assert result["success"] is True
        assert result["response"]["text"] == "Here I am!"

    async def test_wait_timeout_returns_pending(self):
        gw = _gateway()
        ask_result = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Will you answer?",
        )
        result = await gw.wait_for_response(ask_result["request_id"], timeout=0.1)
        assert result["status"] == "pending"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class TestDataTypes:
    def test_human_request_to_dict(self):
        req = HumanRequest(
            type=RequestType.QUESTION,
            question="Test?",
            response_type=ResponseType.CHOICE,
            priority=Priority.P1_HIGH,
        )
        d = req.to_dict()
        assert d["type"] == "question"
        assert d["response_type"] == "choice"
        assert d["priority"] == "P1_HIGH"

    def test_human_request_deadline_auto_set(self):
        req = HumanRequest(sla_hours=1.0)
        assert req.deadline is not None

    def test_tool_schemas_defined(self):
        from src.platform.h2a import H2A_TOOL_SCHEMAS
        names = [s["name"] for s in H2A_TOOL_SCHEMAS]
        assert "human__ask" in names
        assert "human__notify" in names
        assert "human__check" in names
        assert "human__list_available" in names
