"""Tests for the governance hook chain."""

import pytest
from src.core.hooks import (
    AgentContext,
    AuditLogger,
    AuthChecker,
    ComplianceChecker,
    CostTracker,
    HookChain,
    HookDecision,
    RateLimiter,
    SlackNotifier,
    create_hook_chain,
)


def make_context(
    agent_id="test-agent",
    agent_type="doer",
    department="engineering",
    tier=3,
    session_id="session-001",
    allowed_tools=None,
    budget_tokens=500_000,
    model="claude-sonnet-4-5-20250514",
) -> AgentContext:
    return AgentContext(
        agent_id=agent_id,
        agent_type=agent_type,
        department=department,
        tier=tier,
        session_id=session_id,
        allowed_tools=allowed_tools or ["Read", "Edit", "Write", "Bash", "Grep", "Glob"],
        budget_tokens=budget_tokens,
        model=model,
    )


class TestRateLimiter:
    def test_allows_within_limit(self):
        limiter = RateLimiter(max_calls_per_session=10, max_calls_per_minute=5)
        ctx = make_context()
        result = limiter.check(ctx)
        assert result.decision == HookDecision.ALLOW

    def test_blocks_exceeding_session_limit(self):
        limiter = RateLimiter(max_calls_per_session=3, max_calls_per_minute=100)
        ctx = make_context()
        for _ in range(3):
            limiter.check(ctx)
        result = limiter.check(ctx)
        assert result.decision == HookDecision.BLOCK

    def test_reset_clears_counts(self):
        limiter = RateLimiter(max_calls_per_session=2, max_calls_per_minute=100)
        ctx = make_context()
        limiter.check(ctx)
        limiter.check(ctx)
        limiter.reset_session(ctx.session_id)
        result = limiter.check(ctx)
        assert result.decision == HookDecision.ALLOW


class TestAuthChecker:
    def test_allows_permitted_tool(self):
        checker = AuthChecker()
        ctx = make_context(allowed_tools=["Read", "Write"])
        result = checker.check(ctx, "Read")
        assert result.decision == HookDecision.ALLOW

    def test_blocks_unpermitted_tool(self):
        checker = AuthChecker()
        ctx = make_context(allowed_tools=["Read"])
        result = checker.check(ctx, "Write")
        assert result.decision == HookDecision.BLOCK

    def test_blocks_doer_from_agent_tool(self):
        checker = AuthChecker()
        ctx = make_context(tier=3, allowed_tools=["Agent", "Read"])
        result = checker.check(ctx, "Agent")
        assert result.decision == HookDecision.BLOCK

    def test_allows_orchestrator_agent_tool(self):
        checker = AuthChecker()
        ctx = make_context(tier=2, allowed_tools=["Agent", "Read"])
        result = checker.check(ctx, "Agent")
        assert result.decision == HookDecision.ALLOW

    def test_blocks_dangerous_bash_command(self):
        checker = AuthChecker()
        ctx = make_context(allowed_tools=["Bash"])
        result = checker.check(ctx, "Bash", {"command": "rm -rf /"})
        assert result.decision == HookDecision.BLOCK

    def test_allows_safe_bash_command(self):
        checker = AuthChecker()
        ctx = make_context(allowed_tools=["Bash"])
        result = checker.check(ctx, "Bash", {"command": "ls -la"})
        assert result.decision == HookDecision.ALLOW

    def test_wildcard_tool_match(self):
        checker = AuthChecker()
        ctx = make_context(allowed_tools=["mcp__google-workspace__*"])
        result = checker.check(ctx, "mcp__google-workspace__get_events")
        assert result.decision == HookDecision.ALLOW

    def test_wildcard_no_match(self):
        checker = AuthChecker()
        ctx = make_context(allowed_tools=["mcp__google-workspace__*"])
        result = checker.check(ctx, "mcp__stripe__create_charge")
        assert result.decision == HookDecision.BLOCK


class TestCostTracker:
    def test_tracks_cost(self):
        tracker = CostTracker(per_session_limit_usd=10.0)
        ctx = make_context()
        result = tracker.track(ctx, input_tokens=1000, output_tokens=500)
        assert result.decision == HookDecision.ALLOW
        assert tracker.get_session_cost(ctx.session_id) > 0

    def test_blocks_exceeding_budget(self):
        tracker = CostTracker(per_session_limit_usd=0.001)
        ctx = make_context()
        result = tracker.track(ctx, input_tokens=1_000_000, output_tokens=500_000)
        assert result.decision == HookDecision.BLOCK

    def test_tracks_daily_tokens(self):
        tracker = CostTracker()
        ctx = make_context()
        tracker.track(ctx, input_tokens=5000, output_tokens=2000)
        assert tracker.get_daily_tokens() == 7000


class TestComplianceChecker:
    def test_allows_clean_content(self):
        checker = ComplianceChecker()
        result = checker.check_content("Thank you for reaching out. We'll look into this.")
        assert result.decision == HookDecision.ALLOW

    def test_blocks_guaranteed_returns(self):
        checker = ComplianceChecker()
        result = checker.check_content("We guarantee returns of 200% on your investment.")
        assert result.decision == HookDecision.BLOCK

    def test_blocks_exposed_secrets(self):
        checker = ComplianceChecker()
        result = checker.check_content("Your api_key: sk-1234567890abcdef")
        assert result.decision == HookDecision.BLOCK

    def test_email_mass_send_requires_approval(self):
        checker = ComplianceChecker()
        recipients = ", ".join([f"user{i}@example.com" for i in range(100)])
        result = checker.check_email(recipients, "Newsletter", "Hello everyone!")
        assert result.decision == HookDecision.ASK_HUMAN


class TestAuditLogger:
    def test_logs_entry(self):
        logger = AuditLogger()
        ctx = make_context()
        entry = logger.log(ctx, "pre_tool_use", "Read", {"file": "test.py"}, decision="allowed")
        assert entry["agent_id"] == "test-agent"
        assert entry["hook_event"] == "pre_tool_use"
        assert entry["tool_name"] == "Read"
        assert entry["decision"] == "allowed"

    def test_buffer_accumulates(self):
        logger = AuditLogger()
        ctx = make_context()
        logger.log(ctx, "event1", "tool1", {})
        logger.log(ctx, "event2", "tool2", {})
        assert len(logger.get_buffer()) == 2


class TestHookChain:
    def test_full_chain_allows_valid_request(self):
        chain = create_hook_chain()
        ctx = make_context(allowed_tools=["Read"])
        result = chain.pre_tool_use(ctx, "Read")
        assert result.decision == HookDecision.ALLOW

    def test_full_chain_blocks_unauthorized(self):
        chain = create_hook_chain()
        ctx = make_context(allowed_tools=["Read"])
        result = chain.pre_tool_use(ctx, "Write")
        assert result.decision == HookDecision.BLOCK

    def test_post_tool_tracks_cost(self):
        chain = create_hook_chain()
        ctx = make_context(allowed_tools=["Read"])
        result = chain.post_tool_use(
            ctx, "Read", {}, {}, input_tokens=1000, output_tokens=500
        )
        assert result.decision == HookDecision.ALLOW
        assert "session_cost" in result.metadata
