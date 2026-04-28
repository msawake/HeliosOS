"""Tests for platform-generic mechanisms: state machine, unified registry,
escalation chains, behavior profiles, budget multipliers, handoff protocol."""

import pytest
from datetime import datetime, timezone

<<<<<<< HEAD
from src.platform.h2a import (
=======
from src.platform.a2h import (
>>>>>>> origin/main
    BehaviorProfile,
    BudgetMultiplierRule,
    BudgetPolicy,
    DEFAULT_STATES,
    EscalationChain,
    EscalationLevel,
<<<<<<< HEAD
    H2AGateway,
=======
    A2HGateway,
>>>>>>> origin/main
    HandoffItem,
    HandoffRequest,
    HumanAgent,
    HumanStateConfig,
    Status,
)
from src.platform.registry import AgentRegistry
from stacks.base import AgentDefinition, ExecutionType, OwnershipType


# ---------------------------------------------------------------------------
# 1. Human state machine
# ---------------------------------------------------------------------------

class TestHumanStateMachine:
    def test_default_state_is_available(self):
        h = HumanAgent(pid="h:1", name="alice")
        assert h.current_state == "available"
        assert h.accepts_requests is True
        assert h.should_queue is False
        assert h.reroute_target is None

    def test_busy_state_queues_requests(self):
        h = HumanAgent(pid="h:1", name="alice")
        h.set_state("busy")
        assert h.accepts_requests is False
        assert h.should_queue is True

    def test_away_state_reroutes_to_delegate(self):
        h = HumanAgent(pid="h:1", name="alice", delegate="bob")
        h.set_state("away")
        assert h.accepts_requests is False
        assert h.should_queue is False
        assert h.reroute_target == "bob"

    def test_offline_state_reroutes_to_on_call(self):
        h = HumanAgent(pid="h:1", name="alice")
        h.set_state("offline")
        assert h.reroute_target == "on_call"

    def test_custom_domain_states(self):
        call_center_states = {
            "available": HumanStateConfig(accepts_requests=True),
            "in_call": HumanStateConfig(accepts_requests=False, queue_requests=True),
            "on_break": HumanStateConfig(accepts_requests=False, queue_requests=True),
            "off_shift": HumanStateConfig(accepts_requests=False, reroute_to="shift_lead"),
        }
        h = HumanAgent(pid="h:1", name="maria", states_config=call_center_states)

        h.set_state("in_call")
        assert h.accepts_requests is False
        assert h.should_queue is True

        h.set_state("off_shift")
        assert h.reroute_target == "shift_lead"

    def test_set_state_updates_timestamp(self):
        h = HumanAgent(pid="h:1", name="alice")
        assert h.state_changed_at == ""
        h.set_state("busy")
        assert h.state_changed_at != ""

    def test_participant_type_is_human(self):
        h = HumanAgent(pid="h:1", name="alice")
        assert h.participant_type == "human"


# ---------------------------------------------------------------------------
# 2. Unified participant registry
# ---------------------------------------------------------------------------

class TestUnifiedRegistry:
    def test_register_agent_and_human(self):
        reg = AgentRegistry()
        agent = AgentDefinition(name="bot", stack="forgeos",
            execution_type=ExecutionType.REFLEX, ownership=OwnershipType.SHARED)
        reg.register(agent)
        human = HumanAgent(pid="h:alice", name="alice", namespace="sales")
        reg.register_human(human)

        assert len(reg.list_all()) == 1
        assert len(reg.list_humans()) == 1

    def test_list_participants_returns_both(self):
        reg = AgentRegistry()
        agent = AgentDefinition(name="bot", stack="forgeos",
            execution_type=ExecutionType.REFLEX, ownership=OwnershipType.SHARED,
            namespace="sales")
        reg.register(agent)
        reg.register_human(HumanAgent(pid="h:alice", name="alice", namespace="sales"))

        all_p = reg.list_participants()
        assert len(all_p) == 2
        types = {p["participant_type"] for p in all_p}
        assert types == {"agent", "human"}

    def test_filter_by_type(self):
        reg = AgentRegistry()
        reg.register(AgentDefinition(name="bot", stack="forgeos",
            execution_type=ExecutionType.REFLEX, ownership=OwnershipType.SHARED))
        reg.register_human(HumanAgent(pid="h:1", name="alice"))

        agents = reg.list_participants(participant_type="agent")
        humans = reg.list_participants(participant_type="human")
        assert len(agents) == 1
        assert len(humans) == 1
        assert agents[0]["type"] == "agent"
        assert humans[0]["type"] == "human"

    def test_filter_by_namespace(self):
        reg = AgentRegistry()
        reg.register_human(HumanAgent(pid="h:1", name="alice", namespace="sales"))
        reg.register_human(HumanAgent(pid="h:2", name="bob", namespace="finance"))

        sales = reg.list_participants(namespace="sales")
        assert len(sales) == 1
        assert sales[0]["name"] == "alice"

    def test_resolve_human(self):
        reg = AgentRegistry()
        reg.register_human(HumanAgent(pid="h:alice", name="alice", namespace="sales", role="lead"))

        h = reg.resolve_human("sales", "alice")
        assert h is not None
        assert h.role == "lead"
        assert reg.resolve_human("sales", "nobody") is None

    def test_unregister_human(self):
        reg = AgentRegistry()
        reg.register_human(HumanAgent(pid="h:1", name="alice"))
        assert reg.unregister_human("h:1") is True
        assert reg.unregister_human("h:1") is False
        assert len(reg.list_humans()) == 0


# ---------------------------------------------------------------------------
# 3. A2H with state-aware routing
# ---------------------------------------------------------------------------

class TestStateAwareRouting:
    async def test_reroutes_when_human_offline(self):
<<<<<<< HEAD
        gw = H2AGateway()
=======
        gw = A2HGateway()
>>>>>>> origin/main
        alice = HumanAgent(pid="h:alice", name="alice", namespace="eng")
        bob = HumanAgent(pid="h:bob", name="bob", namespace="eng")
        gw.register_human(alice)
        gw.register_human(bob)

        alice.set_state("offline")
        alice.states_config["offline"] = HumanStateConfig(
            accepts_requests=False, reroute_to="bob"
        )

        req = await gw.ask(
            from_agent="a", from_agent_name="bot",
            to_namespace="eng", to_name="alice",
            question="Are you there?",
        )
        assert req.status == Status.PENDING
        assert req.to_human_name == "bob"

    async def test_queues_when_human_busy(self):
<<<<<<< HEAD
        gw = H2AGateway()
=======
        gw = A2HGateway()
>>>>>>> origin/main
        alice = HumanAgent(pid="h:alice", name="alice", namespace="eng")
        gw.register_human(alice)
        alice.set_state("busy")

        req = await gw.ask(
            from_agent="a", from_agent_name="bot",
            to_namespace="eng", to_name="alice",
            question="When you're free?",
        )
        assert req.status == Status.PENDING
        assert req.to_human_name == "alice"


# ---------------------------------------------------------------------------
# 4. Escalation chains
# ---------------------------------------------------------------------------

class TestEscalationChains:
    def test_chain_progression(self):
        chain = EscalationChain(levels=[
            EscalationLevel(target="team_lead", timeout_minutes=5),
            EscalationLevel(target="manager", timeout_minutes=10),
            EscalationLevel(target="director", timeout_minutes=30),
        ])

        assert chain.next_target().target == "team_lead"
        chain.promote()
        assert chain.next_target().target == "manager"
        chain.promote()
        assert chain.next_target().target == "director"
        chain.promote()
        assert chain.next_target() is None

    def test_priority_override(self):
        chain = EscalationChain(levels=[
            EscalationLevel(target="lead", timeout_minutes=2),
            EscalationLevel(target="director", timeout_minutes=5, priority_override="P0_CRITICAL"),
        ])
        chain.promote()
        level = chain.next_target()
        assert level.priority_override == "P0_CRITICAL"

    def test_empty_chain(self):
        chain = EscalationChain()
        assert chain.next_target() is None


# ---------------------------------------------------------------------------
# 5. Behavior profiles
# ---------------------------------------------------------------------------

class TestBehaviorProfiles:
    def test_simple_match(self):
        profile = BehaviorProfile(
            name="night", condition={"shift": "night"},
            overrides={"loop_interval": 30},
        )
        assert profile.evaluate({"shift": "night"}) is True
        assert profile.active is True

    def test_no_match(self):
        profile = BehaviorProfile(
            name="night", condition={"shift": "night"},
            overrides={},
        )
        assert profile.evaluate({"shift": "day"}) is False

    def test_time_range_match(self):
        profile = BehaviorProfile(
            name="night", condition={"hours": "22:00-06:00"},
            overrides={"sensitivity": "high"},
        )
        assert profile.evaluate({"hours": "23:30"}) is True
        assert profile.evaluate({"hours": "03:00"}) is True
        assert profile.evaluate({"hours": "10:00"}) is False

    def test_day_time_range(self):
        profile = BehaviorProfile(
            name="day", condition={"hours": "06:00-22:00"},
            overrides={"sensitivity": "normal"},
        )
        assert profile.evaluate({"hours": "10:00"}) is True
        assert profile.evaluate({"hours": "23:00"}) is False

    def test_multiple_conditions(self):
        profile = BehaviorProfile(
            name="night_weekday",
            condition={"hours": "22:00-06:00", "day_type": "weekday"},
            overrides={},
        )
        assert profile.evaluate({"hours": "23:00", "day_type": "weekday"}) is True
        assert profile.evaluate({"hours": "23:00", "day_type": "weekend"}) is False


# ---------------------------------------------------------------------------
# 6. Budget multipliers
# ---------------------------------------------------------------------------

class TestBudgetMultipliers:
    def test_base_budget_no_rules(self):
        policy = BudgetPolicy(base_daily_usd=8.0)
        assert policy.effective_budget({}) == 8.0

    def test_multiplier_scales_down(self):
        policy = BudgetPolicy(base_daily_usd=8.0, rules=[
            BudgetMultiplierRule(condition={"staffing": "<3"}, multiplier=0.3),
        ])
        assert policy.effective_budget({"staffing": 2}) == pytest.approx(2.4)
        assert policy.effective_budget({"staffing": 6}) == 8.0

    def test_multiplier_scales_up(self):
        policy = BudgetPolicy(base_daily_usd=8.0, rules=[
            BudgetMultiplierRule(condition={"load": ">=100"}, multiplier=2.0),
        ])
        assert policy.effective_budget({"load": 150}) == 16.0
        assert policy.effective_budget({"load": 50}) == 8.0

    def test_first_matching_rule_wins(self):
        policy = BudgetPolicy(base_daily_usd=10.0, rules=[
            BudgetMultiplierRule(condition={"shift": "night"}, multiplier=0.3),
            BudgetMultiplierRule(condition={"shift": "day"}, multiplier=1.0),
        ])
        assert policy.effective_budget({"shift": "night"}) == 3.0
        assert policy.effective_budget({"shift": "day"}) == 10.0


# ---------------------------------------------------------------------------
# 7. Context handoff protocol
# ---------------------------------------------------------------------------

class TestHandoffProtocol:
    def test_handoff_request_creation(self):
        handoff = HandoffRequest(
            from_participant="human:maria",
            to_participant="human:sofia",
            pending_items=[
                HandoffItem(type="escalation", id="E-201", summary="Acme Corp dispute", priority="P1_HIGH"),
                HandoffItem(type="follow_up", id="F-305", summary="Callback for Bob Lee"),
            ],
            context_summary="3 calls resolved, 1 escalation pending, CSAT stable",
        )
        assert len(handoff.pending_items) == 2
        assert handoff.accepted is False

        d = handoff.to_dict()
        assert d["items_count"] == 2
        assert d["from"] == "human:maria"

    def test_handoff_acceptance(self):
        handoff = HandoffRequest(from_participant="a", to_participant="b")
        handoff.accepted = True
        handoff.accepted_at = datetime.now(timezone.utc).isoformat()
        assert handoff.accepted is True

    def test_empty_handoff(self):
        handoff = HandoffRequest(from_participant="a", to_participant="b")
        assert len(handoff.pending_items) == 0
        assert handoff.to_dict()["items_count"] == 0


# ---------------------------------------------------------------------------
# 8. Discovery dict includes participant_type
# ---------------------------------------------------------------------------

class TestDiscoveryDicts:
    def test_human_discovery_has_participant_type(self):
        h = HumanAgent(pid="h:1", name="alice", namespace="eng", role="lead")
        d = h.to_discovery_dict()
        assert d["participant_type"] == "human"
        assert d["type"] == "human"
        assert d["current_state"] == "available"

    def test_registry_participants_have_type(self):
        reg = AgentRegistry()
        reg.register(AgentDefinition(name="bot", stack="forgeos",
            execution_type=ExecutionType.REFLEX, ownership=OwnershipType.SHARED))
        reg.register_human(HumanAgent(pid="h:1", name="alice"))

        for p in reg.list_participants():
            assert "participant_type" in p
