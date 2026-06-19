"""Tests for Helios OS SaaS platform: auth, tenants, billing, persistence, secrets."""

import hashlib
import time
from unittest.mock import MagicMock, patch

import pytest

from src.api.auth import (
    AuthManager,
    AuthUser,
    UserRole,
    generate_api_key,
    hash_api_key,
)
from src.api.tenants import TenantManager
from src.billing.plans import (
    PLANS,
    PLAN_PRICING,
    PlanLimits,
    UsageEnforcer,
    get_plan_limits,
)
from src.billing.stripe_billing import StripeBilling
from src.core.database import DatabaseClient, DatabaseConfig, InMemoryDatabaseClient
from src.core.secrets import SecretsManager
from src.core.hooks import CostTracker, HookDecision, RateLimiter
from src.core.claude_client import ClaudeClient, _run_async_from_thread


# ── Auth ─────────────────────────────────────────────────────────────────


class TestAuthUser:
    def test_admin_can_everything(self):
        user = AuthUser("u1", "a@b.com", "t1", UserRole.ADMIN)
        assert user.can_approve()
        assert user.can_configure()
        assert user.can_view()

    def test_operator_can_approve(self):
        user = AuthUser("u1", "a@b.com", "t1", UserRole.OPERATOR)
        assert user.can_approve()
        assert not user.can_configure()
        assert user.can_view()

    def test_viewer_readonly(self):
        user = AuthUser("u1", "a@b.com", "t1", UserRole.VIEWER)
        assert not user.can_approve()
        assert not user.can_configure()
        assert user.can_view()

    def test_to_dict(self):
        user = AuthUser("u1", "a@b.com", "t1", UserRole.ADMIN, "Alice")
        d = user.to_dict()
        assert d["user_id"] == "u1"
        assert d["email"] == "a@b.com"
        assert d["tenant_id"] == "t1"
        assert d["role"] == "admin"
        assert d["name"] == "Alice"


class TestApiKey:
    def test_generate_api_key(self):
        key = generate_api_key()
        assert key.startswith("fos_")
        assert len(key) > 30

    def test_hash_api_key_deterministic(self):
        key = "fos_test123"
        h1 = hash_api_key(key)
        h2 = hash_api_key(key)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest

    def test_different_keys_different_hashes(self):
        h1 = hash_api_key("fos_key1")
        h2 = hash_api_key("fos_key2")
        assert h1 != h2


class TestAuthManager:
    def test_no_firebase_no_db(self):
        mgr = AuthManager(db_client=None)
        assert mgr.verify_jwt("fake-token") is None
        assert mgr.verify_api_key("fake-key") is None


# ── Tenants ──────────────────────────────────────────────────────────────


class TestTenantManager:
    def test_create_tenant_in_memory(self):
        mgr = TenantManager(db_client=None)
        tenant = mgr.create_tenant("Test Corp", company_type="leadforge", plan="starter")
        assert tenant["name"] == "Test Corp"
        assert tenant["plan"] == "starter"
        assert tenant["company_type"] == "leadforge"
        assert tenant["api_key"].startswith("fos_")
        assert tenant["status"] == "active"
        assert len(tenant["id"]) == 12

    def test_get_tenant_no_db(self):
        mgr = TenantManager(db_client=None)
        assert mgr.get_tenant("nonexistent") is None

    def test_list_tenants_no_db(self):
        mgr = TenantManager(db_client=None)
        assert mgr.list_tenants() == []


# ── Billing Plans ────────────────────────────────────────────────────────


class TestBillingPlans:
    def test_all_plans_defined(self):
        assert "trial" in PLANS
        assert "starter" in PLANS
        assert "growth" in PLANS
        assert "enterprise" in PLANS

    def test_plan_limits_hierarchy(self):
        trial = PLANS["trial"]
        starter = PLANS["starter"]
        growth = PLANS["growth"]
        enterprise = PLANS["enterprise"]

        assert trial.daily_tokens < starter.daily_tokens
        assert starter.daily_tokens < growth.daily_tokens
        assert growth.daily_tokens < enterprise.daily_tokens

        assert trial.max_agents < starter.max_agents
        assert starter.max_agents < growth.max_agents

    def test_get_plan_limits(self):
        limits = get_plan_limits("starter")
        assert limits.daily_tokens == 500_000
        assert limits.max_agents == 10

    def test_unknown_plan_defaults_to_starter(self):
        limits = get_plan_limits("nonexistent")
        assert limits.daily_tokens == PLANS["starter"].daily_tokens

    def test_plan_pricing(self):
        assert PLAN_PRICING["trial"] == 0
        assert PLAN_PRICING["starter"] == 299
        assert PLAN_PRICING["growth"] == 999
        assert PLAN_PRICING["enterprise"] is None  # Custom


class TestUsageEnforcer:
    def test_check_tokens_no_db(self):
        enforcer = UsageEnforcer(db_client=None)
        result = enforcer.check_tokens("t1", "starter")
        assert result["allowed"] is True
        assert result["limit"] == 500_000

    def test_check_workflows_no_db(self):
        enforcer = UsageEnforcer(db_client=None)
        result = enforcer.check_workflows("t1", "starter")
        assert result["allowed"] is True
        assert result["limit"] == 10

    def test_get_usage_summary_no_db(self):
        enforcer = UsageEnforcer(db_client=None)
        summary = enforcer.get_usage_summary("t1")
        assert summary["tokens"] == 0
        assert summary["workflows"] == 0


# ── Stripe Billing ───────────────────────────────────────────────────────


class TestStripeBilling:
    def test_not_enabled_without_key(self):
        billing = StripeBilling(api_key=None)
        assert not billing.is_enabled

    def test_create_customer_disabled(self):
        billing = StripeBilling(api_key=None)
        assert billing.create_customer("t1", "Test", "a@b.com") is None

    def test_create_subscription_disabled(self):
        billing = StripeBilling(api_key=None)
        assert billing.create_subscription("cus_123", "starter", "t1") is None

    def test_create_portal_disabled(self):
        billing = StripeBilling(api_key=None)
        assert billing.create_portal_session("cus_123", "http://localhost") is None


# ── Database ─────────────────────────────────────────────────────────────


class TestDatabaseConfig:
    def test_from_env_defaults(self):
        config = DatabaseConfig()
        assert config.database == "forgeos"
        assert config.min_pool_size == 2
        assert config.max_pool_size == 10

    def test_no_url_returns_disconnected(self):
        db = DatabaseClient(pool=None)
        assert not db.is_connected


class TestInMemoryDatabase:
    def test_not_connected(self):
        db = InMemoryDatabaseClient()
        assert not db.is_connected

    def test_close_safe(self):
        db = InMemoryDatabaseClient()
        db.close()  # Should not raise


# ── Secrets ──────────────────────────────────────────────────────────────


class TestSecretsManager:
    def test_fallback_to_env_var(self):
        import os
        os.environ["TEST_SECRET_KEY"] = "secret123"
        mgr = SecretsManager()
        assert mgr.get("test-secret-key", "") == "secret123"
        del os.environ["TEST_SECRET_KEY"]

    def test_default_value(self):
        mgr = SecretsManager()
        assert mgr.get("nonexistent-secret", "default") == "default"

    def test_anthropic_key_from_env(self):
        import os
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        mgr = SecretsManager()
        assert mgr.get_anthropic_key() == "sk-test"
        del os.environ["ANTHROPIC_API_KEY"]

    def test_tenant_secret(self):
        mgr = SecretsManager()
        # No Secret Manager, no env var → empty
        assert mgr.get_tenant_secret("t1", "custom-key") == ""


# ── CostTracker Pre-Check ───────────────────────────────────────────────


class TestCostTrackerPreCheck:
    def _make_context(self, session_id="s1"):
        from src.core.hooks import AgentContext
        return AgentContext(
            agent_id="test", agent_type="doer", department="sales",
            tier=3, session_id=session_id,
            allowed_tools=["Read"], budget_tokens=10_000,
            model="claude-sonnet-4-5-20250514",
        )

    def test_pre_check_allows_fresh_session(self):
        tracker = CostTracker(per_session_limit_usd=50.0)
        ctx = self._make_context()
        result = tracker.pre_check(ctx)
        assert result.decision == HookDecision.ALLOW

    def test_pre_check_blocks_near_limit(self):
        tracker = CostTracker(per_session_limit_usd=50.0)
        ctx = self._make_context()
        # Simulate spending $49.95 — estimated next call (~$0.12) pushes over $50
        tracker._session_costs["s1"] = 49.95
        result = tracker.pre_check(ctx)
        assert result.decision == HookDecision.BLOCK
        assert "would exceed" in result.reason

    def test_pre_check_allows_under_limit(self):
        tracker = CostTracker(per_session_limit_usd=50.0)
        ctx = self._make_context()
        tracker._session_costs["s1"] = 1.0
        result = tracker.pre_check(ctx)
        assert result.decision == HookDecision.ALLOW


# ── RateLimiter Cleanup ─────────────────────────────────────────────────


class TestRateLimiterCleanup:
    def _make_context(self, session_id):
        from src.core.hooks import AgentContext
        return AgentContext(
            agent_id="test", agent_type="doer", department="sales",
            tier=3, session_id=session_id,
            allowed_tools=["Read"], budget_tokens=10_000,
            model="claude-sonnet-4-5-20250514",
        )

    def test_stale_sessions_cleaned(self):
        rl = RateLimiter()
        # Add an agent entry with old timestamps
        rl._agent_counts["old-session"] = 5
        rl._minute_windows["old-session"] = [time.time() - 7200]  # 2 hours ago

        # Add an agent entry with recent timestamps
        rl._agent_counts["recent-session"] = 3
        rl._minute_windows["recent-session"] = [time.time()]

        rl._cleanup_stale_agents()

        assert "old-session" not in rl._agent_counts
        assert "old-session" not in rl._minute_windows
        assert "recent-session" in rl._agent_counts

    def test_cleanup_triggers_periodically(self):
        rl = RateLimiter()
        ctx = self._make_context("test-session")

        # Run 99 checks — no cleanup yet
        for _ in range(99):
            rl.check(ctx)
        assert rl._check_count == 99

        # Add stale agent entry
        rl._agent_counts["stale"] = 1
        rl._minute_windows["stale"] = [time.time() - 7200]

        # 100th check triggers cleanup
        rl.check(ctx)
        assert "stale" not in rl._agent_counts


# ── Async Tool Execution ────────────────────────────────────────────────


class TestAsyncToolExecution:
    def test_run_async_from_thread_no_loop(self):
        """Verify _run_async_from_thread works from a thread with no event loop."""
        import concurrent.futures

        async def sample_coro():
            return 42

        # Run from a thread pool thread (no event loop) — same as production usage
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(_run_async_from_thread, sample_coro())
            result = future.result(timeout=5)
        assert result == 42


# ── Claude Client Retry ─────────────────────────────────────────────────


class TestClaudeClientRetry:
    def test_retry_on_transient_failure(self):
        """Verify retry logic retries on transient errors (ConnectionError)."""
        mock_client = MagicMock()

        from src.core.model_client import LLMResponse
        mock_response = LLMResponse(
            text="success", tool_calls=[], stop_reason="end_turn",
            input_tokens=100, output_tokens=50,
        )
        mock_client.create_message.side_effect = [
            ConnectionError("transient error"),
            mock_response,
        ]

        client = ClaudeClient(llm_client=mock_client, max_retries=3)
        # Use _call_llm_with_retry directly
        result = client._call_llm_with_retry("model", "system", [], [])
        assert result.text == "success"
        assert mock_client.create_message.call_count == 2

    def test_retry_exhausted_raises(self):
        """Verify transient errors exhaust retries and then raise."""
        mock_client = MagicMock()
        mock_client.create_message.side_effect = ConnectionError("transient error")

        client = ClaudeClient(llm_client=mock_client, max_retries=2)
        with pytest.raises(ConnectionError, match="transient error"):
            client._call_llm_with_retry("model", "system", [], [])
        assert mock_client.create_message.call_count == 2

    def test_fatal_error_no_retry(self):
        """Verify non-transient errors (e.g., bad API key) fail immediately without retrying."""
        mock_client = MagicMock()
        mock_client.create_message.side_effect = ValueError("Invalid API key")

        client = ClaudeClient(llm_client=mock_client, max_retries=3)
        with pytest.raises(ValueError, match="Invalid API key"):
            client._call_llm_with_retry("model", "system", [], [])
        # Should only be called once — no retries for fatal errors
        assert mock_client.create_message.call_count == 1


# ── CompanySystem Persistence Mode ───────────────────────────────────────


class TestCompanySystemPersistence:
    def test_no_db_uses_in_memory(self):
        from forgeos_mcp.integration.custom_tools import CompanySystem, EventBus, KnowledgeBase, MetricsStore
        system = CompanySystem()
        assert isinstance(system.event_bus, EventBus)
        assert isinstance(system.knowledge, KnowledgeBase)
        assert isinstance(system.metrics, MetricsStore)

    def test_disconnected_db_uses_in_memory(self):
        from forgeos_mcp.integration.custom_tools import CompanySystem, EventBus
        mock_db = MagicMock()
        mock_db.is_connected = False
        system = CompanySystem(db_client=mock_db)
        assert isinstance(system.event_bus, EventBus)
