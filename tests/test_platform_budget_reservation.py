"""Tests for BudgetManager two-phase reservation (Phase 1 #3).

The plan identifies a race in the advisory pre-check: concurrent tool
calls can each pass ``check_budget`` before any of them has recorded
cost. ``reserve()`` / ``commit()`` / ``release()`` closes that race by
deducting estimated cost up-front, held against the agent's cap until
the actual cost is known.
"""

from __future__ import annotations

import asyncio

import pytest

from src.platform.kernel import BudgetManager
from src.platform.registry import AgentRegistry
from stacks.base import (
    AgentDefinition,
    ExecutionType,
    OwnershipType,
)


def _registry_with_budget_agent(
    daily_usd: float = 1.00,
    per_task_usd: float | None = None,
    agent_id: str = "pid-budget",
) -> AgentRegistry:
    registry = AgentRegistry()
    agent = AgentDefinition(
        name="budget-agent",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        agent_id=agent_id,
    )
    boundaries = {"budgets": {"daily_usd": daily_usd}}
    if per_task_usd is not None:
        boundaries["budgets"]["per_task_usd"] = per_task_usd
    agent.metadata["_boundaries"] = boundaries
    registry.register(agent)
    return registry


class TestReserveBasic:
    def test_reserve_returns_ticket_and_allows(self):
        reg = _registry_with_budget_agent(daily_usd=10.0)
        bm = BudgetManager(registry=reg)
        ticket, decision = bm.reserve("pid-budget", estimated_cost_usd=0.5)
        assert ticket is not None
        assert decision.allowed
        assert bm.reserved_for("pid-budget") == pytest.approx(0.5)

    def test_reserve_respects_per_task_cap(self):
        reg = _registry_with_budget_agent(daily_usd=10.0, per_task_usd=0.1)
        bm = BudgetManager(registry=reg)
        ticket, decision = bm.reserve("pid-budget", estimated_cost_usd=0.5)
        assert ticket is None
        assert decision.denied
        assert "per-task" in decision.reason

    def test_reserve_respects_daily_cap(self):
        reg = _registry_with_budget_agent(daily_usd=1.0)
        bm = BudgetManager(registry=reg)
        ticket_a, _ = bm.reserve("pid-budget", estimated_cost_usd=0.6)
        assert ticket_a is not None
        ticket_b, decision_b = bm.reserve("pid-budget", estimated_cost_usd=0.5)
        # 0.6 reserved + 0.5 would be 1.1 > 1.0 -> rate_limit
        assert ticket_b is None
        assert decision_b.action == "rate_limit"


class TestCommit:
    def test_commit_releases_reservation(self):
        reg = _registry_with_budget_agent(daily_usd=10.0)
        bm = BudgetManager(registry=reg)
        ticket, _ = bm.reserve("pid-budget", estimated_cost_usd=0.5)
        assert bm.reserved_for("pid-budget") == pytest.approx(0.5)
        bm.commit(ticket, actual_cost_usd=0.3)
        assert bm.reserved_for("pid-budget") == pytest.approx(0.0)

    def test_commit_records_actual_cost_through_usage_enforcer(self):
        recorded: list[dict] = []

        class _FakeUE:
            def record_cost(self, **kwargs):
                recorded.append(kwargs)

            def get_monthly_summary(self, _tenant):
                return {"today_cost_usd": 0.0}

        reg = _registry_with_budget_agent(daily_usd=10.0)
        bm = BudgetManager(registry=reg, usage_enforcer=_FakeUE())
        ticket, _ = bm.reserve("pid-budget", estimated_cost_usd=0.5)
        bm.commit(ticket, actual_cost_usd=0.42, actual_tokens=1200)
        assert recorded and recorded[0]["cost_usd"] == pytest.approx(0.42)
        assert recorded[0]["tokens"] == 1200
        assert recorded[0]["agent_id"] == "pid-budget"

    def test_commit_unknown_ticket_is_idempotent(self):
        bm = BudgetManager()
        decision = bm.commit("nonexistent-ticket", actual_cost_usd=0.1)
        assert decision.allowed
        assert "unknown" in decision.reason


class TestRelease:
    def test_release_frees_reservation(self):
        reg = _registry_with_budget_agent(daily_usd=1.0)
        bm = BudgetManager(registry=reg)
        ticket, _ = bm.reserve("pid-budget", estimated_cost_usd=0.9)
        assert bm.reserved_for("pid-budget") == pytest.approx(0.9)
        bm.release(ticket)
        assert bm.reserved_for("pid-budget") == pytest.approx(0.0)

    def test_release_allows_new_reserve(self):
        reg = _registry_with_budget_agent(daily_usd=1.0)
        bm = BudgetManager(registry=reg)
        ticket_a, _ = bm.reserve("pid-budget", estimated_cost_usd=0.9)
        bm.release(ticket_a)
        # Whole budget is free again.
        ticket_b, decision_b = bm.reserve("pid-budget", estimated_cost_usd=0.9)
        assert ticket_b is not None
        assert decision_b.allowed

    def test_release_unknown_ticket_is_idempotent(self):
        bm = BudgetManager()
        decision = bm.release("nothing")
        assert decision.allowed


# ---------------------------------------------------------------------------
# Concurrency race — the bug the plan calls out at hooks.py:365
# ---------------------------------------------------------------------------


class TestConcurrentReservationRace:
    async def test_two_phase_reservation_prevents_overshoot(self):
        """N=50 concurrent reservations against a $1/day cap cannot overshoot.

        Before this change, ``check_budget`` was advisory: each concurrent
        caller could see the pre-call spend of 0 and pass. With
        reservations, only the first handful succeed, the rest
        rate-limit — matching the plan's acceptance criterion.
        """
        reg = _registry_with_budget_agent(daily_usd=1.0)
        bm = BudgetManager(registry=reg)

        async def try_reserve():
            return bm.reserve("pid-budget", estimated_cost_usd=0.1)

        results = await asyncio.gather(*(try_reserve() for _ in range(50)))
        tickets = [t for (t, _) in results if t is not None]
        # At $0.10 each against a $1 cap, exactly 10 should get tickets.
        assert len(tickets) == 10
        # Reserved total tops out at the cap.
        assert bm.reserved_for("pid-budget") == pytest.approx(1.0)
        # Remaining 40 were rate_limited, not crash-denied.
        rejected_decisions = [d for (t, d) in results if t is None]
        assert all(d.action == "rate_limit" for d in rejected_decisions)
