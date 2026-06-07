"""
Phase 4 tests — worker tier + durable queue + resume service.

The headline test drives the FULL lifecycle through the queue with no blocking:

    trigger enqueues -> worker drives -> kernel ask_human -> worker SUSPENDS and
    is freed (queue empty) -> resume service enqueues on approval -> worker
    resumes -> gated tool executes exactly once -> done.

Plus: ledger CAS exactly-once, duplicate-delivery drop, retry→dead-letter, and
queue priority/delay + rebuild-from-ledger.
"""

from __future__ import annotations

from src.platform.kernel._facade import Kernel
from src.platform.llm_router import LLMResponse, ToolCall
from src.runtime import (
    Enqueuer,
    InMemoryLedger,
    InMemoryRunnableQueue,
    MemoryContinuationStore,
    ResumeService,
    RunnableItem,
    RunStatus,
    StepEngine,
    Worker,
)


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat(self, llm_config, messages, tools=None):
        return self._responses.pop(0)


class FakeToolExecutor:
    def __init__(self, result=None, raises=False):
        self.result = result if result is not None else {"ok": True}
        self.raises = raises
        self.calls = []

    async def execute(self, name, tool_input, agent_context=None):
        self.calls.append((name, dict(tool_input or {})))
        if self.raises:
            raise RuntimeError("boom")
        return self.result


class FakeAgent:
    def __init__(self, agent_id, tools, governance):
        self.agent_id = agent_id
        self.tools = tools
        self.namespace = "default"
        self.name = "a"
        self.metadata = {"_governance": governance}

    def to_dict(self):
        return {"agent_id": self.agent_id}


class FakeRegistry:
    def __init__(self, agents):
        self._a = {a.agent_id: a for a in agents}

    def get(self, aid):
        return self._a.get(aid)

    def list_all(self):
        return list(self._a.values())


def _kernel():
    agent = FakeAgent(
        "agent1", ["notify__email"],
        {"approvals": [{"tool": "notify__email", "mode": "always", "approvers": ["ceo"]}]},
    )
    return Kernel(registry=FakeRegistry([agent]))


def _wire(llm, tx):
    store = MemoryContinuationStore()
    ledger = InMemoryLedger()
    queue = InMemoryRunnableQueue()
    kernel = _kernel()
    engine = StepEngine(llm_router=llm, kernel=kernel, store=store)
    enqueuer = Enqueuer(store=store, ledger=ledger, queue=queue)
    worker = Worker(engine=engine, queue=queue, ledger=ledger, tool_executor=tx)
    resume = ResumeService(store=store, enqueuer=enqueuer, kernel=kernel)
    return store, ledger, queue, kernel, engine, enqueuer, worker, resume


# --------------------------------------------------------------------------
# Headline: full lifecycle through the queue
# --------------------------------------------------------------------------


async def test_full_lifecycle_enqueue_suspend_approve_resume():
    llm = FakeLLM([
        LLMResponse(text="", model="m", provider="anthropic", tokens_used=8,
                    tool_calls=[ToolCall(id="tu1", name="notify__email", input={"to": "ceo@co"})]),
        LLMResponse(text="sent", model="m", provider="anthropic", tokens_used=3),
    ])
    tx = FakeToolExecutor(result={"delivered": True})
    store, ledger, queue, kernel, engine, enqueuer, worker, resume = _wire(llm, tx)

    # Trigger: create the continuation and enqueue it.
    cont = engine.create_continuation(
        pid="agent1", system_prompt="sys", user_prompt="email the ceo",
        provider="anthropic", chat_model="claude-x", tools=[{"name": "notify__email"}],
        source="reflex",
    )
    await enqueuer.enqueue_runnable(cont.continuation_id)
    assert queue.depth() == 1

    # Worker drives -> kernel gates -> SUSPEND. Worker freed, queue drained.
    handled = await worker.run_until_idle()
    assert handled == 1
    assert queue.depth() == 0 and queue.inflight() == 0
    assert tx.calls == []  # nothing executed yet
    assert store.load(cont.continuation_id).status == "suspended"

    # The parked continuation is indexed by the A2H request ref.
    ref = store.load(cont.continuation_id).pending_calls[0].external_ref

    # Approval -> resume service enqueues a p0 resume task.
    await resume.approve(ref, responded_by="ceo-office")
    assert queue.depth() == 1

    # Worker resumes -> gated tool executes once -> done.
    await worker.run_until_idle()
    assert tx.calls == [("notify__email", {"to": "ceo@co"})]
    final = store.load(cont.continuation_id)
    assert final.status == "done" and final.final_output == "sent"


async def test_reject_via_resume_service():
    llm = FakeLLM([
        LLMResponse(text="", model="m", provider="anthropic", tokens_used=8,
                    tool_calls=[ToolCall(id="tu1", name="notify__email", input={"to": "x"})]),
        LLMResponse(text="ok skipped", model="m", provider="anthropic", tokens_used=2),
    ])
    tx = FakeToolExecutor()
    store, ledger, queue, kernel, engine, enqueuer, worker, resume = _wire(llm, tx)
    cont = engine.create_continuation(
        pid="agent1", system_prompt="s", user_prompt="email", provider="anthropic",
        chat_model="claude-x", tools=[{"name": "notify__email"}], source="reflex",
    )
    await enqueuer.enqueue_runnable(cont.continuation_id)
    await worker.run_until_idle()
    ref = store.load(cont.continuation_id).pending_calls[0].external_ref

    await resume.reject(ref, responded_by="ceo")
    await worker.run_until_idle()
    assert tx.calls == []  # never executed
    assert store.load(cont.continuation_id).status == "done"


async def test_duplicate_delivery_drops_via_ledger_cas():
    llm = FakeLLM([
        LLMResponse(text="", model="m", provider="anthropic", tokens_used=8,
                    tool_calls=[ToolCall(id="tu1", name="notify__email", input={"to": "x"})]),
    ])
    tx = FakeToolExecutor()
    store, ledger, queue, kernel, engine, enqueuer, worker, resume = _wire(llm, tx)
    cont = engine.create_continuation(
        pid="agent1", system_prompt="s", user_prompt="email", provider="anthropic",
        chat_model="claude-x", tools=[{"name": "notify__email"}], source="reflex",
    )
    await enqueuer.enqueue_runnable(cont.continuation_id)
    [item] = await queue.claim(count=1)
    epoch = item.enqueue_epoch

    first = await worker.handle_one(item)
    assert first is RunStatus.SUSPENDED
    # A redelivery of the same (cont, epoch) — ledger is no longer queued.
    dup = RunnableItem(cont_id=cont.continuation_id, tenant_id="default", priority="p0",
                       enqueue_epoch=epoch)
    second = await worker.handle_one(dup)
    assert second is None  # dropped by CAS, not re-run


# --------------------------------------------------------------------------
# Ledger CAS unit tests
# --------------------------------------------------------------------------


def test_ledger_cas_single_winner():
    ledger = InMemoryLedger()
    ledger.upsert_queued("c1", tenant_id="t", priority="p1", epoch=1)
    assert ledger.try_mark_running("c1", worker="w1", epoch=1, lease_s=10) is True
    # Same epoch again -> already running -> loses.
    assert ledger.try_mark_running("c1", worker="w2", epoch=1, lease_s=10) is False
    # Stale epoch -> loses.
    ledger.upsert_queued("c1", tenant_id="t", priority="p1", epoch=2)
    assert ledger.try_mark_running("c1", worker="w3", epoch=1, lease_s=10) is False
    assert ledger.try_mark_running("c1", worker="w3", epoch=2, lease_s=10) is True


def test_ledger_retry_then_dead_letter():
    ledger = InMemoryLedger()
    ledger.upsert_queued("c1", tenant_id="t", priority="p1", epoch=1)
    ledger.try_mark_running("c1", worker="w", epoch=1, lease_s=10)
    assert ledger.mark_retryable("c1", error="e", max_crashes=3) is True   # crash 1
    ledger.try_mark_running("c1", worker="w", epoch=1, lease_s=10)
    assert ledger.mark_retryable("c1", error="e", max_crashes=3) is True   # crash 2
    ledger.try_mark_running("c1", worker="w", epoch=1, lease_s=10)
    assert ledger.mark_retryable("c1", error="e", max_crashes=3) is False  # crash 3 -> dead
    assert ledger.get("c1").status == "dead"


# --------------------------------------------------------------------------
# Queue behaviour
# --------------------------------------------------------------------------


async def test_queue_priority_order():
    q = InMemoryRunnableQueue()
    await q.enqueue(RunnableItem(cont_id="low", priority="p2"))
    await q.enqueue(RunnableItem(cont_id="hi", priority="p0"))
    await q.enqueue(RunnableItem(cont_id="mid", priority="p1"))
    claimed = await q.claim(count=3)
    assert [i.cont_id for i in claimed] == ["hi", "mid", "low"]


async def test_queue_delay_not_claimable_until_due():
    import time
    q = InMemoryRunnableQueue()
    await q.enqueue(RunnableItem(cont_id="later", priority="p0"),
                    not_before=time.monotonic() + 100)
    assert await q.claim(count=5) == []  # deferred, not yet due
    assert q.depth() == 1


async def test_rebuild_from_ledger():
    store = MemoryContinuationStore()
    ledger = InMemoryLedger()
    queue = InMemoryRunnableQueue()
    enqueuer = Enqueuer(store=store, ledger=ledger, queue=queue)
    engine = StepEngine(llm_router=FakeLLM([]), kernel=_kernel(), store=store)
    cont = engine.create_continuation(
        pid="agent1", system_prompt="s", user_prompt="u", provider="anthropic",
        chat_model="claude-x", source="reflex",
    )
    await enqueuer.enqueue_runnable(cont.continuation_id)
    # Simulate a Redis flush: drain the live queue, keep the ledger.
    await queue.claim(count=10)
    fresh_queue = InMemoryRunnableQueue()
    enqueuer2 = Enqueuer(store=store, ledger=ledger, queue=fresh_queue)
    assert await enqueuer2.rebuild_from_ledger() == 1
    assert fresh_queue.depth() == 1
