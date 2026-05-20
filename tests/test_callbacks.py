"""Tests for the rich callback system (Phase 2b)."""
import pytest
from src.platform.callbacks import (
    CallbackLevel, CallbackTiming, CallbackDecision,
    CallbackContext, CallbackResult, CallbackRegistry,
)


def _ctx(event_name="tool.execute", agent_id="a1", namespace="default",
         level=CallbackLevel.TOOL, timing=CallbackTiming.BEFORE, **kwargs):
    return CallbackContext(
        agent_id=agent_id, namespace=namespace, level=level,
        timing=timing, event_name=event_name, args=kwargs,
    )


class TestCallbackResult:
    def test_approve(self):
        r = CallbackResult.approve("ok")
        assert r.decision == CallbackDecision.APPROVE
        assert r.reason == "ok"

    def test_deny(self):
        r = CallbackResult.deny("blocked")
        assert r.decision == CallbackDecision.DENY

    def test_modify(self):
        r = CallbackResult.modify({"x": 1}, "changed")
        assert r.decision == CallbackDecision.MODIFY
        assert r.modified_args == {"x": 1}

    def test_defer(self):
        r = CallbackResult.defer("needs human")
        assert r.decision == CallbackDecision.DEFER


class TestCallbackRegistry:
    @pytest.fixture
    def registry(self):
        return CallbackRegistry()

    def test_register_and_count(self, registry):
        reg_id = registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.approve(),
        )
        assert reg_id
        assert registry.count() == 1

    def test_unregister(self, registry):
        reg_id = registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.approve(),
        )
        assert registry.unregister(reg_id) is True
        assert registry.count() == 0
        assert registry.unregister("nonexistent") is False

    async def test_approve_passes_through(self, registry):
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.approve(),
        )
        result = await registry.dispatch(_ctx())
        assert result.decision == CallbackDecision.APPROVE

    async def test_deny_blocks(self, registry):
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.deny("forbidden"),
        )
        result = await registry.dispatch(_ctx())
        assert result.decision == CallbackDecision.DENY
        assert result.reason == "forbidden"

    async def test_modify_changes_args(self, registry):
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.modify({"sanitized": True}),
        )
        result = await registry.dispatch(_ctx())
        assert result.decision == CallbackDecision.MODIFY
        assert result.modified_args == {"sanitized": True}

    async def test_defer_escalates(self, registry):
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.defer("needs approval"),
        )
        result = await registry.dispatch(_ctx())
        assert result.decision == CallbackDecision.DEFER

    async def test_deny_wins_over_approve(self, registry):
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.approve(), priority=1,
        )
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.deny("nope"), priority=2,
        )
        result = await registry.dispatch(_ctx())
        assert result.decision == CallbackDecision.DENY

    async def test_deny_wins_regardless_of_order(self, registry):
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.modify({"x": 1}), priority=1,
        )
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.deny("blocked"), priority=50,
        )
        result = await registry.dispatch(_ctx())
        assert result.decision == CallbackDecision.DENY

    async def test_priority_ordering(self, registry):
        calls = []
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: (calls.append("second"), CallbackResult.approve())[1],
            priority=200,
        )
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: (calls.append("first"), CallbackResult.approve())[1],
            priority=100,
        )
        await registry.dispatch(_ctx())
        assert calls == ["first", "second"]

    async def test_agent_filter(self, registry):
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.deny("only for a2"),
            agent_filter="a2",
        )
        result = await registry.dispatch(_ctx(agent_id="a1"))
        assert result.decision == CallbackDecision.APPROVE  # not matched
        result = await registry.dispatch(_ctx(agent_id="a2"))
        assert result.decision == CallbackDecision.DENY  # matched

    async def test_namespace_filter(self, registry):
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.deny("sales only"),
            namespace_filter="sales",
        )
        result = await registry.dispatch(_ctx(namespace="finance"))
        assert result.decision == CallbackDecision.APPROVE
        result = await registry.dispatch(_ctx(namespace="sales"))
        assert result.decision == CallbackDecision.DENY

    async def test_wildcard_event(self, registry):
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "*",
            lambda ctx: CallbackResult.approve("matched all"),
        )
        result = await registry.dispatch(_ctx(event_name="anything"))
        assert result.decision == CallbackDecision.APPROVE
        assert result.reason == "matched all"

    async def test_async_callback(self, registry):
        async def async_cb(ctx):
            return CallbackResult.deny("async denied")

        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute", async_cb,
        )
        result = await registry.dispatch(_ctx())
        assert result.decision == CallbackDecision.DENY
        assert result.reason == "async denied"

    async def test_no_callbacks_returns_approve(self, registry):
        result = await registry.dispatch(_ctx())
        assert result.decision == CallbackDecision.APPROVE

    async def test_level_mismatch_not_triggered(self, registry):
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.BEFORE, "model.chat",
            lambda ctx: CallbackResult.deny("model only"),
        )
        result = await registry.dispatch(_ctx(level=CallbackLevel.TOOL))
        assert result.decision == CallbackDecision.APPROVE

    async def test_callback_exception_is_caught(self, registry):
        def bad_cb(ctx):
            raise RuntimeError("boom")

        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute", bad_cb,
        )
        result = await registry.dispatch(_ctx())
        assert result.decision == CallbackDecision.APPROVE  # exception skipped

    def test_clear(self, registry):
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.approve(),
        )
        registry.clear()
        assert registry.count() == 0
