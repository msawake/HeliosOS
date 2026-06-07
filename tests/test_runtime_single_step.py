"""
Per-turn (single-step) runtime tests.

When the worker tier is active the StepEngine runs in ``single_step`` mode: one
LLM turn per worker claim. A turn that appends tool results returns
``CONTINUE`` and the worker re-enqueues the continuation so a FRESH worker runs
the next turn — "one LLM turn == one runnable Redis task". A final response
ends the run; an ``ask_human`` gate suspends it.

These tests lock:
  * single-step turn outcomes (DONE / CONTINUE / SUSPENDED),
  * the edge case (assistant text AND a tool call must dispatch, not finalize),
  * worker re-enqueue exactly-once (the tool fires once across turns),
  * a duplicate same-epoch redelivery is dropped (no double-run),
  * verbose kernel logging emits only when enabled.
"""

from __future__ import annotations

import logging

from src.platform.capabilities import CapabilityManager
from src.platform.kernel._facade import KernelDecision
from src.platform.llm_router import LLMResponse, ToolCall
from src.runtime import (
    Enqueuer,
    InMemoryLedger,
    InMemoryRunnableQueue,
    MemoryContinuationStore,
    Resolution,
    ResolutionOutcome,
    RunStatus,
    StepEngine,
    SuspendReason,
    Worker,
)


# --------------------------------------------------------------------------
# Test doubles (mirror tests/test_runtime_engine.py)
# --------------------------------------------------------------------------


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, llm_config, messages, tools=None):
        self.calls += 1
        if not self._responses:
            return LLMResponse(text="(no more)", model="m", provider="anthropic")
        return self._responses.pop(0)


class FakeTool:
    def __init__(self, result=None):
        self.result = result if result is not None else {"ok": True}
        self.calls = []

    async def execute(self, name, tool_input, agent_context=None):
        self.calls.append((name, dict(tool_input or {})))
        return self.result


class FakeKernel:
    def __init__(self, *, approve_tools=()):
        self.approve_tools = set(approve_tools)
        self.capabilities_mgr = CapabilityManager()

    def issue_capability(self, **kw):
        return self.capabilities_mgr.issue(**kw)

    def syscall(self, *, verb, subject, object, args=None, dispatcher=None):
        args = args or {}
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


def _tool(tool_name, tool_input=None, tool_id="tu_1", text=""):
    return LLMResponse(
        text=text, model="m", provider="anthropic", tokens_used=10,
        input_tokens=7, output_tokens=3,
        tool_calls=[ToolCall(id=tool_id, name=tool_name, input=tool_input or {})],
    )


def _final(text="done"):
    return LLMResponse(text=text, model="m", provider="anthropic", tokens_used=5)


def _ss_engine(llm, kernel, store):
    return StepEngine(llm_router=llm, kernel=kernel, store=store, single_step=True)


# --------------------------------------------------------------------------
# Engine-level single-step semantics
# --------------------------------------------------------------------------


async def test_single_step_final_text_is_done_one_call():
    store = MemoryContinuationStore()
    llm = FakeLLM([_final("hi there")])
    eng = _ss_engine(llm, FakeKernel(), store)
    cont = eng.create_continuation(
        pid="a1", system_prompt="s", user_prompt="hi", provider="anthropic",
        chat_model="claude-x",
    )
    out = await eng.drive(cont.continuation_id)
    assert out.status is RunStatus.DONE
    assert out.output == "hi there"
    assert llm.calls == 1  # exactly one LLM turn


async def test_single_step_tool_returns_continue_then_done():
    store = MemoryContinuationStore()
    tx = FakeTool(result={"hits": 1})
    llm = FakeLLM([_tool("search__files", {"q": "x"}), _final("found")])
    eng = _ss_engine(llm, FakeKernel(), store)
    cont = eng.create_continuation(
        pid="a1", system_prompt="s", user_prompt="find", provider="anthropic",
        chat_model="claude-x", tools=[{"name": "search__files"}],
    )
    cid = cont.continuation_id

    # Turn 1: tool dispatched -> CONTINUE (the worker would re-enqueue here).
    out1 = await eng.drive(cid, tool_executor=tx)
    assert out1.status is RunStatus.CONTINUE
    assert llm.calls == 1
    assert tx.calls == [("search__files", {"q": "x"})]
    assert store.load(cid).step_index == 1  # advanced for the next worker

    # Turn 2: a fresh drive does exactly one more LLM call and completes.
    out2 = await eng.drive(cid, tool_executor=tx)
    assert out2.status is RunStatus.DONE
    assert out2.output == "found"
    assert llm.calls == 2
    assert len(tx.calls) == 1  # tool not re-run


async def test_single_step_text_plus_tool_call_continues_not_finalize():
    """EDGE CASE: an assistant turn carrying BOTH text and a tool call must
    dispatch the tool and continue — it must NOT finalize at the text."""
    store = MemoryContinuationStore()
    tx = FakeTool()
    llm = FakeLLM([
        _tool("search__files", {"q": "x"}, text="Let me check tool X"),
        _final("answer"),
    ])
    eng = _ss_engine(llm, FakeKernel(), store)
    cont = eng.create_continuation(
        pid="a1", system_prompt="s", user_prompt="q", provider="anthropic",
        chat_model="claude-x", tools=[{"name": "search__files"}],
    )
    out = await eng.drive(cont.continuation_id, tool_executor=tx)
    # NOT done with "Let me check tool X" — it dispatched the tool and continues.
    assert out.status is RunStatus.CONTINUE
    assert tx.calls == [("search__files", {"q": "x"})]


async def test_single_step_ask_human_suspends_with_args():
    store = MemoryContinuationStore()
    tx = FakeTool()
    llm = FakeLLM([_tool("notify__email", {"to": "ceo@co", "subject": "Q2"})])
    eng = _ss_engine(llm, FakeKernel(approve_tools={"notify__email"}), store)
    cont = eng.create_continuation(
        pid="a1", system_prompt="s", user_prompt="email", provider="anthropic",
        chat_model="claude-x", tools=[{"name": "notify__email"}],
    )
    out = await eng.drive(cont.continuation_id, tool_executor=tx)
    assert out.status is RunStatus.SUSPENDED
    assert tx.calls == []  # gated tool not executed
    assert len(out.pending) == 1
    p = out.pending[0]
    assert p["name"] == "notify__email"
    # pending now carries the captured args so the CLI can show recipient/subject.
    assert p["arguments"] == {"to": "ceo@co", "subject": "Q2"}


async def test_single_step_resume_one_turn_token_short_circuits():
    store = MemoryContinuationStore()
    tx = FakeTool(result={"delivered": True})
    kernel = FakeKernel(approve_tools={"notify__email"})
    llm = FakeLLM([_tool("notify__email", {"to": "ceo@co"}), _final("sent")])
    eng = _ss_engine(llm, kernel, store)
    cont = eng.create_continuation(
        pid="a1", system_prompt="s", user_prompt="email", provider="anthropic",
        chat_model="claude-x", tools=[{"name": "notify__email"}],
    )
    out = await eng.drive(cont.continuation_id, tool_executor=tx)
    assert out.status is RunStatus.SUSPENDED
    assert llm.calls == 1

    token = kernel.issue_capability(subject="a1", target="tool:notify__email", verb="tool.call")
    resumed = await eng.resume(
        Resolution(continuation_id=out.continuation_id, tool_use_id=out.pending[0]["tool_use_id"],
                   outcome=ResolutionOutcome.ACCEPT, capability_token=token.id),
        tool_executor=tx,
    )
    # Resume injects the approved result and does exactly ONE more LLM turn.
    assert resumed.status is RunStatus.DONE
    assert resumed.output == "sent"
    assert tx.calls == [("notify__email", {"to": "ceo@co"})]  # ran exactly once
    assert llm.calls == 2


# --------------------------------------------------------------------------
# Worker re-enqueue (exactly-once across per-turn boundaries)
# --------------------------------------------------------------------------


def _worker_stack(llm, kernel, tx):
    store = MemoryContinuationStore()
    ledger = InMemoryLedger()
    queue = InMemoryRunnableQueue()
    enqueuer = Enqueuer(store=store, ledger=ledger, queue=queue)
    engine = StepEngine(llm_router=llm, kernel=kernel, store=store, single_step=True)
    worker = Worker(engine=engine, queue=queue, ledger=ledger, enqueuer=enqueuer,
                    tool_executor=tx)
    return store, ledger, queue, enqueuer, engine, worker


async def test_worker_continue_reenqueues_and_runs_tool_once():
    tx = FakeTool()
    llm = FakeLLM([_tool("search__files", {"q": "x"}), _final("found")])
    store, ledger, queue, enqueuer, engine, worker = _worker_stack(llm, FakeKernel(), tx)
    cont = engine.create_continuation(
        pid="a1", system_prompt="s", user_prompt="find", provider="anthropic",
        chat_model="claude-x", tools=[{"name": "search__files"}],
    )
    cid = cont.continuation_id
    await enqueuer.enqueue_runnable(cid)

    handled = await worker.run_until_idle()

    # Two turns => two separate worker claims (one LLM call each).
    assert handled == 2
    assert llm.calls == 2
    assert tx.calls == [("search__files", {"q": "x"})]  # tool ran exactly once
    final = store.load(cid)
    assert final.status == "done"
    assert final.final_output == "found"
    # Queue drained, ledger row settled.
    assert ledger.get(cid).status == "done"


async def test_worker_drops_duplicate_same_epoch_delivery():
    """A redelivery of the SAME (cont_id, epoch) after the turn already claimed
    it is dropped by the ledger CAS — no double-run."""
    tx = FakeTool()
    llm = FakeLLM([_tool("search__files", {"q": "x"}), _final("found")])
    store, ledger, queue, enqueuer, engine, worker = _worker_stack(llm, FakeKernel(), tx)
    cont = engine.create_continuation(
        pid="a1", system_prompt="s", user_prompt="find", provider="anthropic",
        chat_model="claude-x", tools=[{"name": "search__files"}],
    )
    cid = cont.continuation_id
    await enqueuer.enqueue_runnable(cid)

    # Claim the first item, then push a duplicate of it back before handling.
    items = await queue.claim(count=1)
    assert len(items) == 1
    dup = items[0]
    # First handling: runs turn 1, re-enqueues turn 2 at epoch+1.
    st1 = await worker.handle_one(dup)
    assert st1 is RunStatus.CONTINUE
    # Re-deliver the SAME (stale-epoch) item: dropped, no execution.
    st_dup = await worker.handle_one(dup)
    assert st_dup is None  # stale/duplicate -> dropped

    # Finish the run.
    await worker.run_until_idle()
    assert tx.calls == [("search__files", {"q": "x"})]  # still exactly once
    assert store.load(cid).status == "done"


# --------------------------------------------------------------------------
# Verbose kernel logging
# --------------------------------------------------------------------------


def test_kernel_verbose_logs_only_when_enabled(monkeypatch, caplog):
    from src.platform.kernel import _syscall as sc

    def _pm_allow():
        class PM:
            def check_tool_call(self, s, o, ti):
                return KernelDecision.allow(reason="acl ok")
        return PM()

    syscall = sc.Syscall(verb="tool.call", subject="a1", object="notify__email",
                         args={"tool_input": {"to": "x"}})

    # Flag OFF -> no [kernel] lines.
    monkeypatch.setattr(sc, "KERNEL_VERBOSE", False)
    with caplog.at_level(logging.INFO, logger="src.platform.kernel._syscall"):
        sc.SyscallPipeline({"capability": sc.make_capability_stage(_pm_allow())}).run(syscall)
    assert not any("[kernel]" in r.message for r in caplog.records)

    caplog.clear()

    # Flag ON -> per-stage + DECISION lines present.
    monkeypatch.setattr(sc, "KERNEL_VERBOSE", True)
    with caplog.at_level(logging.INFO, logger="src.platform.kernel._syscall"):
        sc.SyscallPipeline({"capability": sc.make_capability_stage(_pm_allow())}).run(syscall)
    msgs = [r.message for r in caplog.records]
    assert any("[kernel] syscall verb=tool.call" in m for m in msgs)
    assert any("[kernel] DECISION" in m and "allow" in m for m in msgs)


def test_kernel_verbose_logs_capability_token_short_circuit(monkeypatch, caplog):
    from src.platform.capabilities import CapabilityManager
    from src.platform.kernel import _syscall as sc

    caps = CapabilityManager()
    tok = caps.issue(subject="a1", target="tool:notify__email", verb="tool.call", ttl_seconds=60)
    syscall = sc.Syscall(verb="tool.call", subject="a1", object="notify__email",
                         args={"tool_input": {"to": "x"}, "capability_token": tok.id})

    monkeypatch.setattr(sc, "KERNEL_VERBOSE", True)
    with caplog.at_level(logging.INFO, logger="src.platform.kernel._syscall"):
        sc.SyscallPipeline(
            {"capability": sc.make_capability_stage(None, caps)}
        ).run(syscall)
    assert any("capability TOKEN short-circuit" in r.message for r in caplog.records)
