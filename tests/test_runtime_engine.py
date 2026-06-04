"""
Phase 1 tests for the durable continuation runtime (in-memory).

Exercises the core mechanic: a tool call is admitted by the kernel; when the
kernel returns ``ask_human`` the engine SUSPENDS (persists a continuation,
returns a suspended outcome) instead of blocking; on approval the gated tool
executes through the same syscall path (a capability token flips the decision
to ``allow``) and its result is injected into the exact tool_use slot, and the
loop resumes to completion.
"""

from __future__ import annotations

from src.platform.capabilities import CapabilityManager
from src.platform.kernel._facade import KernelDecision
from src.platform.kernel._process import AgentIdentity, Phase, ProcessTable
from src.platform.llm_router import LLMResponse, ToolCall
from src.runtime import (
    MemoryContinuationStore,
    Resolution,
    ResolutionOutcome,
    RunStatus,
    StepEngine,
    SuspendReason,
)


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------


class FakeLLM:
    """Returns scripted LLMResponses, one per chat() call."""

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, llm_config, messages, tools=None) -> LLMResponse:
        self.calls += 1
        if not self._responses:
            return LLMResponse(text="(no more scripted responses)", model="m", provider="anthropic")
        return self._responses.pop(0)


class FakeToolExecutor:
    """Records every execute() call so tests can assert exactly-once."""

    def __init__(self, result=None):
        self.result = result if result is not None else {"ok": True}
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name, tool_input, agent_context=None):
        self.calls.append((name, dict(tool_input or {})))
        return self.result


class FakeKernel:
    """Kernel stub: certain tools require human approval; a valid capability
    token (minted on approval) flips ``ask_human`` to ``allow``. Uses a real
    CapabilityManager so resume-token validation is genuine."""

    def __init__(self, *, approve_tools=(), deny_tools=()):
        self.approve_tools = set(approve_tools)
        self.deny_tools = set(deny_tools)
        self.capabilities_mgr = CapabilityManager()

    def issue_capability(self, **kw):
        return self.capabilities_mgr.issue(**kw)

    def syscall(self, *, verb, subject, object, args=None, dispatcher=None):
        args = args or {}
        if object in self.deny_tools:
            return KernelDecision.deny(reason="explicitly denied", tool=object)
        if object in self.approve_tools:
            token = args.get("capability_token")
            if token and self.capabilities_mgr.authorize(
                token_id=token, subject=subject, target=f"tool:{object}", verb="tool.call",
            ):
                return KernelDecision.allow(reason="authorized by token")
            return KernelDecision.ask_human(
                reason=f"tool '{object}' requires human approval",
                captured_action={"verb": "tool.call", "tool_name": object,
                                  "tool_input": args.get("tool_input")},
                suspend_reason=SuspendReason.HUMAN_APPROVAL,
            )
        return KernelDecision.allow(reason="permitted")


def _tool_response(tool_name, tool_input=None, tool_id="tu_1"):
    return LLMResponse(
        text="",
        model="m",
        provider="anthropic",
        tokens_used=10,
        input_tokens=7,
        output_tokens=3,
        tool_calls=[ToolCall(id=tool_id, name=tool_name, input=tool_input or {})],
    )


def _final_response(text="all done"):
    return LLMResponse(text=text, model="m", provider="anthropic",
                       tokens_used=5, input_tokens=5, output_tokens=0)


def _engine(llm, kernel, store=None, process_table=None):
    return StepEngine(
        llm_router=llm,
        kernel=kernel,
        store=store or MemoryContinuationStore(),
        process_table=process_table,
    )


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


async def test_plain_tool_then_done_no_suspension():
    """A non-gated tool runs inline; the loop completes normally."""
    llm = FakeLLM([_tool_response("search__files", {"q": "x"}), _final_response("found it")])
    kernel = FakeKernel()  # nothing requires approval
    tx = FakeToolExecutor(result={"hits": 3})
    eng = _engine(llm, kernel)

    out = await eng.run(
        pid="agent1", system_prompt="sys", user_prompt="find x",
        provider="anthropic", chat_model="claude-x", tools=[{"name": "search__files"}],
        tool_executor=tx,
    )

    assert out.status is RunStatus.DONE
    assert out.output == "found it"
    assert tx.calls == [("search__files", {"q": "x"})]


async def test_ask_human_suspends_and_frees_worker():
    """A gated tool makes the engine suspend (persist + return), not block."""
    llm = FakeLLM([_tool_response("notify__email", {"to": "ceo@co"})])
    kernel = FakeKernel(approve_tools={"notify__email"})
    tx = FakeToolExecutor()
    store = MemoryContinuationStore()
    pt = ProcessTable()
    pt.register(AgentIdentity(pid="agent1", name="a", namespace="default"), spec_ref="agent1",
                phase=Phase.RUNNING)
    eng = _engine(llm, kernel, store=store, process_table=pt)

    out = await eng.run(
        pid="agent1", system_prompt="sys", user_prompt="email the ceo",
        provider="anthropic", chat_model="claude-x", tools=[{"name": "notify__email"}],
        tool_executor=tx,
    )

    assert out.status is RunStatus.SUSPENDED
    assert out.suspend_reason == SuspendReason.HUMAN_APPROVAL
    assert len(out.pending) == 1 and out.pending[0]["name"] == "notify__email"
    # The gated tool did NOT execute.
    assert tx.calls == []
    # Continuation persisted + findable by the A2H request ref.
    ref = out.pending[0]["external_ref"]
    cont = store.find_by_external_ref(ref)
    assert cont is not None and cont.status == "suspended"
    # Process parked in AWAITING_HUMAN.
    assert pt.get("agent1").phase is Phase.AWAITING_HUMAN


async def test_approve_resumes_executes_once_and_completes():
    """On accept, the gated tool executes exactly once and the loop resumes."""
    llm = FakeLLM([_tool_response("notify__email", {"to": "ceo@co"}), _final_response("sent")])
    kernel = FakeKernel(approve_tools={"notify__email"})
    tx = FakeToolExecutor(result={"delivered": True})
    store = MemoryContinuationStore()
    eng = _engine(llm, kernel, store=store)

    out = await eng.run(
        pid="agent1", system_prompt="sys", user_prompt="email the ceo",
        provider="anthropic", chat_model="claude-x", tools=[{"name": "notify__email"}],
        tool_executor=tx,
    )
    assert out.status is RunStatus.SUSPENDED
    pending = out.pending[0]

    # Simulate the approval handler: mint a capability token for the gated tool.
    token = kernel.issue_capability(
        subject="agent1", target="tool:notify__email", verb="tool.call", ttl_seconds=3600,
    )
    resolution = Resolution(
        continuation_id=out.continuation_id,
        tool_use_id=pending["tool_use_id"],
        outcome=ResolutionOutcome.ACCEPT,
        capability_token=token.id,
        responded_by="ceo-office",
    )
    resumed = await eng.resume(resolution, tool_executor=tx)

    assert resumed.status is RunStatus.DONE
    assert resumed.output == "sent"
    # Executed exactly once, with the originally-captured arguments.
    assert tx.calls == [("notify__email", {"to": "ceo@co"})]
    # The message history carries a normal tool_result for that tool_use id.
    cont = store.load(out.continuation_id)
    tool_results = [
        b for m in cont.messages if isinstance(m.get("content"), list)
        for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert any(b["tool_use_id"] == pending["tool_use_id"] for b in tool_results)


async def test_reject_does_not_execute_and_injects_error():
    """On reject, the tool is never executed and an error tool_result is injected."""
    llm = FakeLLM([_tool_response("notify__email", {"to": "ceo@co"}), _final_response("ok, skipped")])
    kernel = FakeKernel(approve_tools={"notify__email"})
    tx = FakeToolExecutor()
    store = MemoryContinuationStore()
    eng = _engine(llm, kernel, store=store)

    out = await eng.run(
        pid="agent1", system_prompt="sys", user_prompt="email the ceo",
        provider="anthropic", chat_model="claude-x", tools=[{"name": "notify__email"}],
        tool_executor=tx,
    )
    resolution = Resolution(
        continuation_id=out.continuation_id,
        tool_use_id=out.pending[0]["tool_use_id"],
        outcome=ResolutionOutcome.REJECT,
    )
    resumed = await eng.resume(resolution, tool_executor=tx)

    assert resumed.status is RunStatus.DONE
    assert tx.calls == []  # never executed
    cont = store.load(out.continuation_id)
    tool_results = [
        b for m in cont.messages if isinstance(m.get("content"), list)
        for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert tool_results and tool_results[0]["is_error"] is True


async def test_resume_is_idempotent():
    """A duplicate resume (double-click / webhook redelivery) is a no-op and
    does not double-execute the gated tool."""
    llm = FakeLLM([_tool_response("notify__email", {"to": "ceo@co"}), _final_response("sent")])
    kernel = FakeKernel(approve_tools={"notify__email"})
    tx = FakeToolExecutor()
    store = MemoryContinuationStore()
    eng = _engine(llm, kernel, store=store)

    out = await eng.run(
        pid="agent1", system_prompt="sys", user_prompt="email the ceo",
        provider="anthropic", chat_model="claude-x", tools=[{"name": "notify__email"}],
        tool_executor=tx,
    )
    token = kernel.issue_capability(subject="agent1", target="tool:notify__email", verb="tool.call")
    resolution = Resolution(
        continuation_id=out.continuation_id,
        tool_use_id=out.pending[0]["tool_use_id"],
        outcome=ResolutionOutcome.ACCEPT,
        capability_token=token.id,
    )
    first = await eng.resume(resolution, tool_executor=tx)
    assert first.status is RunStatus.DONE
    # Second delivery: continuation is no longer suspended -> rejected no-op.
    second = await eng.resume(resolution, tool_executor=tx)
    assert second.status is RunStatus.FAILED
    assert len(tx.calls) == 1  # executed exactly once across both resumes


async def test_openai_dialect_resume_injects_tool_message():
    """For the OpenAI dialect, resume appends a `tool` role message keyed by id."""
    llm = FakeLLM([
        LLMResponse(text="", model="gpt-x", provider="openai", tokens_used=4,
                    tool_calls=[ToolCall(id="call_9", name="notify__email", input={"to": "x"})]),
        LLMResponse(text="done", model="gpt-x", provider="openai", tokens_used=2),
    ])
    kernel = FakeKernel(approve_tools={"notify__email"})
    tx = FakeToolExecutor(result={"delivered": True})
    store = MemoryContinuationStore()
    eng = _engine(llm, kernel, store=store)

    out = await eng.run(
        pid="agent1", system_prompt="sys", user_prompt="email", provider="openai",
        chat_model="gpt-x", tools=[{"name": "notify__email"}], tool_executor=tx,
    )
    token = kernel.issue_capability(subject="agent1", target="tool:notify__email", verb="tool.call")
    resumed = await eng.resume(
        Resolution(continuation_id=out.continuation_id, tool_use_id="call_9",
                   outcome=ResolutionOutcome.ACCEPT, capability_token=token.id),
        tool_executor=tx,
    )
    assert resumed.status is RunStatus.DONE
    cont = store.load(out.continuation_id)
    tool_msgs = [m for m in cont.messages if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "call_9"


async def test_denied_tool_returns_error_result_not_suspend():
    """A denied tool yields an error tool_result; the loop continues (no park)."""
    llm = FakeLLM([_tool_response("shell__exec", {"cmd": "rm"}), _final_response("blocked, moving on")])
    kernel = FakeKernel(deny_tools={"shell__exec"})
    tx = FakeToolExecutor()
    eng = _engine(llm, kernel)

    out = await eng.run(
        pid="agent1", system_prompt="sys", user_prompt="run", provider="anthropic",
        chat_model="claude-x", tools=[{"name": "shell__exec"}], tool_executor=tx,
    )
    assert out.status is RunStatus.DONE
    assert tx.calls == []  # denied, never executed
