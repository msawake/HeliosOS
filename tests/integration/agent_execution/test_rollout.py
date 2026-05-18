"""Tests for progressive rollout — canary deploys."""

import pytest

from src.platform.rollout import (
    Rollout,
    RolloutManager,
    RolloutState,
    RolloutStrategy,
    route_to_canary,
)


@pytest.fixture
def manager():
    return RolloutManager()


@pytest.fixture
def strategy():
    return RolloutStrategy(
        type="canary",
        canary_percent=10,
        canary_duration_hours=0.001,  # very short for testing
        success_threshold=0.95,
        auto_promote=True,
        auto_rollback=True,
    )


class TestRouteToCanary:
    def test_deterministic_routing(self):
        result1 = route_to_canary("session-abc", 50)
        result2 = route_to_canary("session-abc", 50)
        assert result1 == result2

    def test_percent_0_never_routes(self):
        for i in range(100):
            assert route_to_canary(f"session-{i}", 0) is False

    def test_percent_100_always_routes(self):
        for i in range(100):
            assert route_to_canary(f"session-{i}", 100) is True

    def test_roughly_correct_distribution(self):
        canary_count = sum(route_to_canary(f"s-{i}", 20) for i in range(1000))
        assert 100 < canary_count < 300  # ~20% with some variance


class TestRolloutStrategy:
    def test_from_dict(self):
        s = RolloutStrategy.from_dict({
            "type": "canary",
            "canary_percent": 25,
            "canary_duration_hours": 12,
        })
        assert s.canary_percent == 25
        assert s.canary_duration_hours == 12

    def test_from_dict_none(self):
        s = RolloutStrategy.from_dict(None)
        assert s.type == "immediate"


class TestRolloutManager:
    async def test_start_canary(self, manager, strategy):
        rollout = await manager.start_canary(
            agent_name="lead-scorer",
            namespace="sales",
            strategy=strategy,
            original_agent_id="orig-123",
            canary_agent_id="canary-456",
        )
        assert rollout.state == RolloutState.CANARY_ACTIVE
        assert rollout.original_agent_id == "orig-123"
        assert rollout.canary_agent_id == "canary-456"

    async def test_record_invocation(self, manager, strategy):
        rollout = await manager.start_canary(
            "test-agent", "default", strategy, "orig", "canary"
        )
        manager.record_invocation(rollout.rollout_id, is_canary=True, success=True)
        manager.record_invocation(rollout.rollout_id, is_canary=True, success=True)
        manager.record_invocation(rollout.rollout_id, is_canary=True, success=False)

        assert rollout.canary_invocations == 3
        assert rollout.canary_errors == 1
        assert rollout.canary_error_rate == pytest.approx(1 / 3, abs=0.01)

    async def test_auto_promote_after_duration(self, manager, strategy):
        rollout = await manager.start_canary(
            "agent", "ns", strategy, "orig", "canary"
        )
        # Simulate successful invocations
        for _ in range(20):
            manager.record_invocation(rollout.rollout_id, is_canary=True, success=True)

        # Force the started_at to the past so duration is elapsed
        from datetime import datetime, timedelta, timezone
        rollout.started_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        decision = await manager.check_and_decide(rollout.rollout_id)
        assert decision == "promoted"
        assert rollout.state == RolloutState.PROMOTED

    async def test_auto_rollback_on_high_error_rate(self, manager):
        strategy = RolloutStrategy(
            type="canary", canary_percent=10,
            canary_duration_hours=1.0,  # long duration so it doesn't auto-promote
            success_threshold=0.95,
            auto_rollback=True,
        )
        rollout = await manager.start_canary(
            "agent", "ns", strategy, "orig", "canary"
        )
        # Simulate 50% error rate (well above 5% threshold)
        for _ in range(5):
            manager.record_invocation(rollout.rollout_id, is_canary=True, success=True)
        for _ in range(5):
            manager.record_invocation(rollout.rollout_id, is_canary=True, success=False)

        decision = await manager.check_and_decide(rollout.rollout_id)
        assert decision == "rolled_back"
        assert rollout.state == RolloutState.ROLLED_BACK

    async def test_continue_when_healthy_but_duration_not_met(self, manager):
        strategy = RolloutStrategy(
            type="canary", canary_duration_hours=100.0  # very long
        )
        rollout = await manager.start_canary(
            "agent", "ns", strategy, "orig", "canary"
        )
        manager.record_invocation(rollout.rollout_id, is_canary=True, success=True)

        decision = await manager.check_and_decide(rollout.rollout_id)
        assert decision == "continue"

    async def test_get_active_rollout(self, manager, strategy):
        await manager.start_canary("agent", "sales", strategy, "o", "c")
        assert manager.get_active_rollout("agent", "sales") is not None
        assert manager.get_active_rollout("other", "sales") is None

    async def test_list_rollouts(self, manager, strategy):
        await manager.start_canary("a1", "ns", strategy, "o1", "c1")
        await manager.start_canary("a2", "ns", strategy, "o2", "c2")

        active = manager.list_rollouts(RolloutState.CANARY_ACTIVE)
        assert len(active) == 2

        all_rollouts = manager.list_rollouts()
        assert len(all_rollouts) == 2
