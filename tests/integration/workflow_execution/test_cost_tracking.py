"""Tests for cost tracking in the agentic loop + pricing table."""

from __future__ import annotations

import pytest

from src.billing.plans import (
    PLANS,
    TOKEN_PRICING,
    UsageEnforcer,
    estimate_cost_usd,
    get_plan_limits,
)


class TestPricing:
    def test_claude_sonnet_pricing(self):
        # 1M input tokens = $3, 1M output = $15
        cost = estimate_cost_usd("claude-sonnet-4-5", 1_000_000, 0)
        assert cost == pytest.approx(3.0, rel=0.01)

        cost = estimate_cost_usd("claude-sonnet-4-5", 0, 1_000_000)
        assert cost == pytest.approx(15.0, rel=0.01)

    def test_gpt_4o_pricing(self):
        cost = estimate_cost_usd("gpt-4o", 1_000_000, 0)
        assert cost == pytest.approx(2.5, rel=0.01)

    def test_unknown_model_uses_default(self):
        cost = estimate_cost_usd("unknown-model-xyz", 1_000_000, 1_000_000)
        # default pricing: 3 + 15 = 18
        assert cost == pytest.approx(18.0, rel=0.01)

    def test_empty_model(self):
        assert estimate_cost_usd("", 1000, 1000) == 0.0

    def test_zero_tokens(self):
        assert estimate_cost_usd("claude-opus-4-6", 0, 0) == 0.0

    def test_longest_prefix_match(self):
        """'claude-3-5-sonnet-20241022' should match 'claude-3-5-sonnet'."""
        cost = estimate_cost_usd("claude-3-5-sonnet-20241022", 1_000_000, 0)
        assert cost == pytest.approx(3.0, rel=0.01)


class TestPlanLimits:
    def test_all_plans_have_limits(self):
        for name in ("trial", "starter", "growth", "enterprise"):
            limits = get_plan_limits(name)
            assert limits.daily_tokens > 0
            assert limits.max_agents > 0

    def test_unknown_plan_falls_back_to_starter(self):
        limits = get_plan_limits("imaginary")
        assert limits.daily_tokens == PLANS["starter"].daily_tokens


class TestUsageEnforcerInMemory:
    def test_no_db_check_always_allowed(self):
        enforcer = UsageEnforcer(db_client=None)
        result = enforcer.check_tokens("t1", "starter")
        # With no DB, used=0 and limit=500000 → allowed
        assert result["allowed"] is True
        assert result["used"] == 0

    def test_no_db_record_is_noop(self):
        enforcer = UsageEnforcer(db_client=None)
        enforcer.record_usage("t1", "tokens", 1000)  # Should not raise
        summary = enforcer.get_usage_summary("t1")
        assert summary["tokens"] == 0

    def test_check_monthly_cost_no_limit(self):
        enforcer = UsageEnforcer(db_client=None)
        result = enforcer.check_monthly_cost("t1", None)
        assert result["allowed"] is True

    def test_check_monthly_cost_with_limit(self):
        enforcer = UsageEnforcer(db_client=None)
        result = enforcer.check_monthly_cost("t1", 100.0)
        # No usage → allowed
        assert result["allowed"] is True
        assert result["limit_usd"] == 100.0
        assert result["remaining"] == 100.0


class TestAgenticLoopCostTracking:
    """Integration: run a loop with a mock LLM + enforcer and verify recording."""

    @pytest.mark.asyncio
    async def test_records_tokens_and_cost(self):
        from src.platform.agentic_loop import run_agentic_loop
        from src.platform.llm_router import LLMResponse
        from stacks.base import LLMConfig

        class MockRouter:
            async def chat(self, llm_config, messages, tools=None):
                return LLMResponse(
                    text="done",
                    model="claude-sonnet-4-5",
                    provider="anthropic",
                    tokens_used=1000,
                )

        recorded: list[tuple] = []

        class MockEnforcer:
            def check_tokens(self, t, p, **_k):
                return {"allowed": True, "used": 0, "limit": 500000, "remaining": 500000, "overage": 0}

            def check_monthly_cost(self, t, limit):
                return {"allowed": True, "cost_usd": 0, "limit_usd": limit, "remaining": limit}

            def record_usage(self, tenant_id, metric, amount):
                recorded.append((tenant_id, metric, amount))

        class MockToolExecutor:
            _usage_enforcer = MockEnforcer()

        cfg = LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic")
        result = await run_agentic_loop(
            llm_router=MockRouter(),
            llm_config=cfg,
            system_prompt="You are a test agent.",
            user_prompt="hi",
            tool_executor=MockToolExecutor(),
            agent_context={"tenant_id": "acme", "plan": "starter"},
        )

        assert result.output == "done"
        # Verify the enforcer saw tokens, cost, and agent invocation
        metrics = {r[1] for r in recorded}
        assert "tokens" in metrics
        assert "cost_usd" in metrics
        assert "agent_invocations" in metrics

    @pytest.mark.asyncio
    async def test_daily_limit_exceeded_short_circuits(self):
        from src.platform.agentic_loop import run_agentic_loop
        from stacks.base import AgentStatus, LLMConfig

        class MockEnforcer:
            def check_tokens(self, t, p, **_k):
                return {"allowed": False, "used": 999999, "limit": 500000, "remaining": 0}

            def check_monthly_cost(self, t, limit):
                return {"allowed": True, "cost_usd": 0}

            def record_usage(self, *a, **kw):
                pass

        class MockRouter:
            async def chat(self, *a, **kw):
                raise RuntimeError("Should not be called")

        class MockToolExecutor:
            _usage_enforcer = MockEnforcer()

        cfg = LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic")
        result = await run_agentic_loop(
            llm_router=MockRouter(),
            llm_config=cfg,
            system_prompt="",
            user_prompt="hi",
            tool_executor=MockToolExecutor(),
            agent_context={"tenant_id": "acme", "plan": "starter"},
        )
        assert result.status == AgentStatus.FAILED
        assert "limit exceeded" in (result.error or "").lower()
