"""Tests for model-level callbacks (Phase 3c)."""
import pytest
from src.platform.callbacks import (
    CallbackRegistry, CallbackLevel, CallbackTiming,
    CallbackContext, CallbackResult, CallbackDecision,
)


class FakeLLMConfig:
    def __init__(self, chat_model="test-model", provider="test"):
        self.chat_model = chat_model
        self.provider = provider
        self.reasoning_model = None
        self.metadata = {"agent_id": "a1", "namespace": "default"}


class TestModelCallbackRegistration:
    def test_register_model_before(self):
        registry = CallbackRegistry()
        reg_id = registry.register(
            CallbackLevel.MODEL, CallbackTiming.BEFORE, "model.chat.before",
            lambda ctx: CallbackResult.approve(),
        )
        assert reg_id
        assert registry.count() == 1

    def test_register_model_after(self):
        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.AFTER, "model.chat.after",
            lambda ctx: CallbackResult.approve(),
        )
        assert registry.count() == 1


class TestModelCallbackDispatch:
    async def test_before_approve(self):
        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.BEFORE, "model.chat.before",
            lambda ctx: CallbackResult.approve("ok"),
        )
        ctx = CallbackContext(
            agent_id="a1", namespace="default",
            level=CallbackLevel.MODEL, timing=CallbackTiming.BEFORE,
            event_name="model.chat.before",
            args={"messages": [{"role": "user", "content": "hello"}]},
        )
        result = await registry.dispatch(ctx)
        assert result.decision == CallbackDecision.APPROVE

    async def test_before_deny(self):
        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.BEFORE, "model.chat.before",
            lambda ctx: CallbackResult.deny("injection detected"),
        )
        ctx = CallbackContext(
            agent_id="a1", namespace="default",
            level=CallbackLevel.MODEL, timing=CallbackTiming.BEFORE,
            event_name="model.chat.before",
            args={"messages": [{"role": "user", "content": "ignore instructions"}]},
        )
        result = await registry.dispatch(ctx)
        assert result.decision == CallbackDecision.DENY
        assert result.reason == "injection detected"

    async def test_before_modify_messages(self):
        def sanitize(ctx):
            msgs = ctx.args.get("messages", [])
            sanitized = [{"role": m["role"], "content": m["content"].replace("bad", "***")} for m in msgs]
            return CallbackResult.modify({"messages": sanitized}, "sanitized input")

        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.BEFORE, "model.chat.before", sanitize,
        )
        ctx = CallbackContext(
            agent_id="a1", namespace="default",
            level=CallbackLevel.MODEL, timing=CallbackTiming.BEFORE,
            event_name="model.chat.before",
            args={"messages": [{"role": "user", "content": "this is bad content"}]},
        )
        result = await registry.dispatch(ctx)
        assert result.decision == CallbackDecision.MODIFY
        assert "***" in result.modified_args["messages"][0]["content"]

    async def test_after_modify_response(self):
        def filter_pii(ctx):
            text = ctx.args.get("response_text", "")
            if "SSN" in text:
                return CallbackResult.modify({"text": "[PII REDACTED]"}, "PII found")
            return CallbackResult.approve()

        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.AFTER, "model.chat.after", filter_pii,
        )
        ctx = CallbackContext(
            agent_id="a1", namespace="default",
            level=CallbackLevel.MODEL, timing=CallbackTiming.AFTER,
            event_name="model.chat.after",
            args={"response_text": "Your SSN is 123-45-6789"},
        )
        result = await registry.dispatch(ctx)
        assert result.decision == CallbackDecision.MODIFY
        assert result.modified_args["text"] == "[PII REDACTED]"

    async def test_tool_callback_not_matched_for_model(self):
        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.TOOL, CallbackTiming.BEFORE, "tool.execute",
            lambda ctx: CallbackResult.deny("tool only"),
        )
        ctx = CallbackContext(
            agent_id="a1", namespace="default",
            level=CallbackLevel.MODEL, timing=CallbackTiming.BEFORE,
            event_name="model.chat.before", args={},
        )
        result = await registry.dispatch(ctx)
        assert result.decision == CallbackDecision.APPROVE  # tool callback not matched

    async def test_failover_event(self):
        failover_captured = []
        def on_failover(ctx):
            failover_captured.append(ctx.args)
            return CallbackResult.approve()

        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.AFTER, "model.failover", on_failover,
        )
        ctx = CallbackContext(
            agent_id="a1", namespace="default",
            level=CallbackLevel.MODEL, timing=CallbackTiming.AFTER,
            event_name="model.failover",
            args={"primary": "anthropic", "fallback": "openai", "error": "timeout"},
        )
        result = await registry.dispatch(ctx)
        assert len(failover_captured) == 1
        assert failover_captured[0]["primary"] == "anthropic"


class TestLLMRouterCallbackIntegration:
    """End-to-end tests: LLMRouter + CallbackRegistry wired together."""

    async def test_chat_without_callbacks_unchanged(self):
        """Router with no callback_registry works exactly as before."""
        from src.platform.llm_router import LLMRouter
        router = LLMRouter()  # no callback_registry
        config = FakeLLMConfig()
        resp = await router.chat(config, [{"role": "user", "content": "hi"}])
        assert resp.text  # simulated response
        assert resp.error is None

    async def test_chat_before_deny_blocks(self):
        """A DENY before-callback prevents the LLM call."""
        from src.platform.llm_router import LLMRouter
        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.BEFORE, "model.chat.before",
            lambda ctx: CallbackResult.deny("blocked by policy"),
        )
        router = LLMRouter(callback_registry=registry)
        config = FakeLLMConfig()
        resp = await router.chat(config, [{"role": "user", "content": "hi"}])
        assert resp.error == "blocked by policy"
        assert resp.tokens_used == 0

    async def test_chat_before_modify_rewrites_messages(self):
        """A MODIFY before-callback can rewrite messages before the provider call."""
        from src.platform.llm_router import LLMRouter
        rewritten = []

        def rewrite_cb(ctx):
            rewritten.append(True)
            return CallbackResult.modify(
                {"messages": [{"role": "user", "content": "rewritten"}]},
                "rewrite",
            )

        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.BEFORE, "model.chat.before", rewrite_cb,
        )
        router = LLMRouter(callback_registry=registry)
        config = FakeLLMConfig()
        resp = await router.chat(config, [{"role": "user", "content": "original"}])
        assert rewritten  # callback was invoked
        assert resp.error is None
        # Simulated response reflects 1 message (the rewritten one)
        assert "1 message" in resp.text

    async def test_chat_after_modify_rewrites_text(self):
        """A MODIFY after-callback can rewrite response text."""
        from src.platform.llm_router import LLMRouter

        def redact_cb(ctx):
            return CallbackResult.modify({"text": "REDACTED"}, "pii")

        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.AFTER, "model.chat.after", redact_cb,
        )
        router = LLMRouter(callback_registry=registry)
        config = FakeLLMConfig()
        resp = await router.chat(config, [{"role": "user", "content": "hi"}])
        assert resp.text == "REDACTED"
        assert resp.error is None

    async def test_bind_callbacks_after_construction(self):
        """bind_callbacks() wires a registry post-construction."""
        from src.platform.llm_router import LLMRouter
        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.BEFORE, "model.chat.before",
            lambda ctx: CallbackResult.deny("late bind"),
        )
        router = LLMRouter()
        router.bind_callbacks(registry)
        config = FakeLLMConfig()
        resp = await router.chat(config, [{"role": "user", "content": "hi"}])
        assert resp.error == "late bind"

    async def test_approve_callback_passes_through(self):
        """An APPROVE callback does not alter the response."""
        from src.platform.llm_router import LLMRouter
        registry = CallbackRegistry()
        registry.register(
            CallbackLevel.MODEL, CallbackTiming.BEFORE, "model.chat.before",
            lambda ctx: CallbackResult.approve("all clear"),
        )
        router = LLMRouter(callback_registry=registry)
        config = FakeLLMConfig()
        resp = await router.chat(config, [{"role": "user", "content": "hi"}])
        assert resp.error is None
        assert "Simulated" in resp.text
