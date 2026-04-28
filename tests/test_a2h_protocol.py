"""Tests for the A2H protocol — ForgeOS implementation."""

import pytest

from src.platform.a2h import (
    DashboardChannel,
    DelegationRule,
    A2HGateway,
    HumanAgent,
    HumanRequest,
    HumanResponse,
    HumanStateConfig,
    InMemoryHumanRequestStore,
    Notification,
    Priority,
    Status,
    RequestType,
    ResponseType,
)


def _gateway() -> A2HGateway:
    gw = A2HGateway()
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
        delegation_rules=[
            DelegationRule(
                name="auto_small",
                from_name_pattern="sales-*",
                response_type="approval",
                context_conditions={"value": {"lt": 10000}},
                auto_response={"approved": True, "reason": "Auto: under $10K"},
            ),
        ],
    ))
    return gw


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

    def test_resolve_missing(self):
        gw = _gateway()
        assert gw.resolve_human("engineering", "nobody") is None

    def test_unregister(self):
        gw = _gateway()
        assert gw.unregister_human("human:jama") is True
        assert gw.resolve_human("engineering", "jama") is None

    def test_discovery_dict(self):
        h = HumanAgent(pid="human:test", name="test", namespace="eng", role="dev")
        d = h.to_discovery_dict()
        assert d["type"] == "human"
        assert d["participant_type"] == "human"
        assert d["stack"] == "human"

    def test_participant_card(self):
        h = HumanAgent(pid="h:1", name="sarah", namespace="sales", role="VP",
                        channels=["dashboard", "slack"])
        card = h.to_card()
        assert card["protocol"] == "a2h/v1"
        assert card["participant_type"] == "human"
        assert card["a2h"]["channels"] == ["dashboard", "slack"]


class TestAskReturnsObject:
    async def test_ask_returns_human_request(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="agent-1", from_agent_name="bot",
            to_namespace="engineering", to_name="jama",
            question="Approve?", response_type="approval",
        )
        assert isinstance(req, HumanRequest)
        assert req.status == Status.PENDING
        assert req.id.startswith("req_")
        assert req.protocol == "a2h/v1"
        assert req.response_type == ResponseType.APPROVAL

    async def test_ask_unknown_returns_cancelled(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="eng", to_name="nobody",
            question="Hello?",
        )
        assert req.status == Status.CANCELLED

    async def test_ask_with_context(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Deal?", context={"deal_value": 50000},
        )
        assert req.context["deal_value"] == 50000

    async def test_priority_lowercase(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Urgent!", priority="critical",
        )
        assert req.priority == Priority.CRITICAL
        assert req.priority.value == "critical"


class TestRespond:
    async def test_respond_creates_structured_response(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Yes?", response_type="confirm",
        )
        result = gw.respond(req.id, {"confirmed": True, "text": "Yes indeed"})
        assert result["success"] is True

        updated = gw.get_request(req.id)
        assert updated["status"] == "answered"
        assert updated["response"]["confirmed"] is True
        assert updated["response"]["channel"] == "dashboard"

    async def test_respond_nonexistent_fails(self):
        gw = _gateway()
        result = gw.respond("nonexistent", {"text": "hi"})
        assert result["success"] is False

    async def test_double_respond_fails(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Once?",
        )
        gw.respond(req.id, {"text": "first"})
        result = gw.respond(req.id, {"text": "second"})
        assert result["success"] is False


class TestCancel:
    async def test_cancel_pending(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Cancel me",
        )
        result = gw.cancel(req.id, reason="No longer needed")
        assert result["success"] is True

        updated = gw.get_request(req.id)
        assert updated["status"] == "cancelled"

    async def test_cancel_answered_fails(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Answer then cancel",
        )
        gw.respond(req.id, {"text": "done"})
        result = gw.cancel(req.id)
        assert result["success"] is False


class TestNotifications:
    async def test_notify_returns_notification_object(self):
        gw = _gateway()
        notif = await gw.notify(
            from_agent="a", from_agent_name="reporter",
            to_namespace="sales", to_name="sarah",
            message="Daily report ready",
        )
        assert isinstance(notif, Notification)
        assert notif.id.startswith("notif_")
        assert notif.protocol == "a2h/v1"
        assert notif.message == "Daily report ready"

    async def test_notify_unknown_human(self):
        gw = _gateway()
        notif = await gw.notify(
            from_agent="a", from_agent_name="b",
            to_namespace="sales", to_name="nobody",
            message="Hello?",
        )
        assert "UNDELIVERABLE" in notif.message


class TestAutoDelegation:
    async def test_auto_approve_matching_rule(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="sales-pipeline-agent",
            to_namespace="finance", to_name="auto-approver",
            question="Approve $5K?", response_type="approval",
            context={"value": 5000},
        )
        assert req.status == Status.AUTO_DELEGATED
        assert req.response is not None
        assert req.response.approved is True
        assert req.response.channel == "auto_delegation"

    async def test_over_limit_stays_pending(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="sales-pipeline-agent",
            to_namespace="finance", to_name="auto-approver",
            question="Approve $50K?", response_type="approval",
            context={"value": 50000},
        )
        assert req.status == Status.PENDING

    async def test_wrong_agent_stays_pending(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="random-agent",
            to_namespace="finance", to_name="auto-approver",
            question="Approve?", response_type="approval",
            context={"value": 100},
        )
        assert req.status == Status.PENDING


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
        all_pending = gw.list_pending()
        assert len(all_pending) == 3


class TestAsyncWait:
    async def test_wait_returns_after_response(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="b",
            to_namespace="engineering", to_name="jama",
            question="Waiting...",
        )

        import asyncio
        async def _respond_later():
            await asyncio.sleep(0.1)
            gw.respond(req.id, {"text": "Here I am!"})

        asyncio.create_task(_respond_later())
        result = await gw.wait_for_response(req.id, timeout=5.0)
        assert result["success"] is True
        assert result["response"]["text"] == "Here I am!"


class TestSerialization:
    async def test_request_to_dict_has_protocol(self):
        gw = _gateway()
        req = await gw.ask(
            from_agent="a", from_agent_name="bot",
            to_namespace="engineering", to_name="jama",
            question="Test?", priority="high",
        )
        d = req.to_dict()
        assert d["protocol"] == "a2h/v1"
        assert d["from"]["participant_type"] == "agent"
        assert d["to"]["participant_type"] == "human"
        assert d["content"]["question"] == "Test?"
        assert d["priority"] == "high"
        assert d["status"] == "pending"

    def test_response_to_dict(self):
        r = HumanResponse(value="approve", text="OK", approved=True, channel="slack")
        d = r.to_dict()
        assert d["value"] == "approve"
        assert d["approved"] is True
        assert d["channel"] == "slack"

    def test_notification_to_dict(self):
        n = Notification(from_agent_name="bot", to_human_name="sarah",
                          message="Hello", severity="info")
        d = n.to_dict()
        assert d["protocol"] == "a2h/v1"
        assert d["type"] == "notification"


class TestStatusEnum:
    def test_all_spec_statuses_exist(self):
        assert Status.CREATED.value == "created"
        assert Status.PENDING.value == "pending"
        assert Status.ANSWERED.value == "answered"
        assert Status.EXPIRED.value == "expired"
        assert Status.CANCELLED.value == "cancelled"
        assert Status.ESCALATED.value == "escalated"
        assert Status.AUTO_DELEGATED.value == "auto_delegated"

    def test_priority_lowercase(self):
        assert Priority.CRITICAL.value == "critical"
        assert Priority.HIGH.value == "high"
        assert Priority.MEDIUM.value == "medium"
        assert Priority.LOW.value == "low"

    def test_form_response_type(self):
        assert ResponseType.FORM.value == "form"
