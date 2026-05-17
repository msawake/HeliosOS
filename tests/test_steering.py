"""Tests for steering handlers / GUIDE decision."""
import pytest
from src.platform.callbacks import (
    CallbackDecision, CallbackResult, CallbackRegistry,
    CallbackLevel, CallbackTiming, CallbackContext,
)


class TestGuideDecision:
    def test_guide_in_enum(self):
        assert CallbackDecision.GUIDE == "guide"

    def test_guide_factory(self):
        r = CallbackResult.guide("Add a WHERE clause")
        assert r.decision == CallbackDecision.GUIDE
        assert r.reason == "Add a WHERE clause"

    async def test_guide_precedence_over_approve(self):
        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.approve(), priority=1,
        )
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.guide("use LIMIT"), priority=2,
        )
        ctx = CallbackContext(
            agent_id="a1", namespace="default",
            level=CallbackLevel.TOOL, timing=CallbackTiming.BEFORE,
            event_name="tool.execute", args={},
        )
        result = await registry.dispatch(ctx)
        assert result.decision == CallbackDecision.GUIDE
        assert "LIMIT" in result.reason

    async def test_deny_wins_over_guide(self):
        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.guide("try this"), priority=1,
        )
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.deny("forbidden"), priority=2,
        )
        ctx = CallbackContext(
            agent_id="a1", namespace="default",
            level=CallbackLevel.TOOL, timing=CallbackTiming.BEFORE,
            event_name="tool.execute", args={},
        )
        result = await registry.dispatch(ctx)
        assert result.decision == CallbackDecision.DENY

    async def test_guide_alone(self):
        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.guide("Break into smaller queries"),
        )
        ctx = CallbackContext(
            agent_id="a1", namespace="default",
            level=CallbackLevel.TOOL, timing=CallbackTiming.BEFORE,
            event_name="tool.execute", args={"tool_name": "run_sql"},
        )
        result = await registry.dispatch(ctx)
        assert result.decision == CallbackDecision.GUIDE
        assert result.reason == "Break into smaller queries"


class TestSteeringInToolExecution:
    """Test that GUIDE integrates with tool execution flow."""

    def test_guide_result_has_guidance_flag(self):
        """When GUIDE is returned, the tool result should contain guidance."""
        result = CallbackResult.guide("Add error handling")
        # Simulate what the agentic loop would produce
        tool_result = {"error": f"Guidance: {result.reason}", "guidance": True}
        assert tool_result["guidance"] is True
        assert "Add error handling" in tool_result["error"]

    async def test_guidance_max_retries_escalates_to_deny(self):
        """After MAX_GUIDANCE_RETRIES, guidance escalates to deny."""
        from src.platform.agentic_loop import _check_guidance, MAX_GUIDANCE_RETRIES

        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.guide("use LIMIT"),
        )

        guidance_counts: dict[str, int] = {}
        agent_context = {"agent_id": "a1", "namespace": "default"}

        # First MAX_GUIDANCE_RETRIES - 1 should return guidance
        for i in range(MAX_GUIDANCE_RETRIES - 1):
            result = await _check_guidance(
                registry, "run_sql", {"query": "SELECT *"}, agent_context, guidance_counts,
            )
            assert result is not None
            assert result.get("guidance") is True
            assert "LIMIT" in result["error"]

        # The next one should escalate to deny
        result = await _check_guidance(
            registry, "run_sql", {"query": "SELECT *"}, agent_context, guidance_counts,
        )
        assert result is not None
        assert result.get("denied") is True
        assert "guided 3 times" in result["error"]

    async def test_no_callback_registry_skips_guidance(self):
        """When no callback registry is provided, _check_guidance returns None."""
        from src.platform.agentic_loop import _check_guidance

        result = await _check_guidance(
            None, "run_sql", {"query": "SELECT *"}, None, {},
        )
        assert result is None

    async def test_approve_callback_skips_guidance(self):
        """When callback returns APPROVE, no guidance is returned."""
        from src.platform.agentic_loop import _check_guidance

        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.approve(),
        )

        guidance_counts: dict[str, int] = {}
        result = await _check_guidance(
            registry, "run_sql", {"query": "SELECT *"},
            {"agent_id": "a1", "namespace": "default"}, guidance_counts,
        )
        assert result is None

    async def test_deny_callback_returns_denied(self):
        """When callback returns DENY, _check_guidance returns denied dict."""
        from src.platform.agentic_loop import _check_guidance

        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.deny("forbidden tool"),
        )

        guidance_counts: dict[str, int] = {}
        result = await _check_guidance(
            registry, "run_sql", {"query": "DROP TABLE"},
            {"agent_id": "a1", "namespace": "default"}, guidance_counts,
        )
        assert result is not None
        assert result.get("denied") is True
        assert "forbidden tool" in result["error"]
