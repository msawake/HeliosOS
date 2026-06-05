"""
Worker-tier integration test (deterministic — no Qwen, no Redis).

Drives the full RuntimeService loop with in-memory backends + a fake LLM:
enqueue_invoke -> worker claims off the queue -> StepEngine drives -> kernel
gates notify__email -> run parks (worker freed) -> ResumeService.approve
re-enqueues -> worker resumes -> gated tool runs once -> done.

Proves the worker pool actually consumes the runnable queue (not inline).
"""

from __future__ import annotations

import asyncio

from src.platform.kernel._facade import Kernel
from src.platform.llm_router import LLMResponse, ToolCall
from src.runtime import (
    InMemoryLedger,
    InMemoryRunnableQueue,
    MemoryContinuationStore,
    RuntimeService,
)
from stacks.base import AgentDefinition, ExecutionType, LLMConfig, OwnershipType


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat(self, llm_config, messages, tools=None):
        # Tool turn first, final turn after resume.
        return self._responses.pop(0) if self._responses else LLMResponse(
            text="(done)", model="m", provider="anthropic")


class FakeTool:
    def __init__(self):
        self.calls = []

    async def execute(self, name, tool_input, agent_context=None):
        self.calls.append((name, dict(tool_input or {})))
        return {"delivered": True}

    # build_tool_definitions checks for these; returning [] is fine — the
    # FakeLLM emits the tool call regardless of declared schemas.
    def get_custom_tool_definitions(self):
        return [{"name": "notify__email", "description": "send", "input_schema": {"type": "object"}}]


class FakeRegistry:
    def __init__(self, agent_def):
        self._a = {agent_def.agent_id: agent_def}

    def get(self, aid):
        return self._a.get(aid)

    def list_all(self):
        return list(self._a.values())


def _agent_def():
    d = AgentDefinition(
        name="risk-auditor", stack="forgeos", execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED, agent_id="pid-rt",
        llm_config=LLMConfig(chat_model="claude-x", provider="anthropic"),
        tools=["notify__email"], system_prompt="audit", namespace="default",
    )
    d.metadata["_governance"] = {
        "approvals": [{"tool": "notify__email", "mode": "always", "approvers": ["ceo"]}]
    }
    return d


async def _wait_until(fn, timeout=5.0):
    async def loop():
        while not fn():
            await asyncio.sleep(0.05)
    await asyncio.wait_for(loop(), timeout=timeout)


async def test_worker_tier_enqueue_drive_pause_approve_resume():
    agent = _agent_def()
    registry = FakeRegistry(agent)
    kernel = Kernel(registry=registry)
    llm = FakeLLM([
        LLMResponse(text="", model="m", provider="anthropic", tokens_used=5,
                    tool_calls=[ToolCall(id="tu1", name="notify__email", input={"to": "ceo@co"})]),
        LLMResponse(text="sent", model="m", provider="anthropic", tokens_used=2),
    ])
    tx = FakeTool()
    store = MemoryContinuationStore()
    svc = RuntimeService(
        kernel=kernel, llm_router=llm, tool_executor=tx, registry=registry,
        store=store, ledger=InMemoryLedger(), queue=InMemoryRunnableQueue(), workers=2,
    )
    await svc.start()
    try:
        # Enqueue an invocation — returns a run handle; workers drive it.
        run_id = await svc.enqueue_invoke(agent, "audit and email the ceo")

        # Worker claims off the queue, drives, and parks on the approval.
        await _wait_until(lambda: store.load(run_id) and store.load(run_id).status == "suspended")
        cont = store.load(run_id)
        assert cont.suspend_reason == "human_approval"
        assert tx.calls == []  # gated tool NOT executed yet
        req = cont.pending_calls[0].external_ref
        assert req

        # Approve via the resume service -> re-enqueues -> worker resumes.
        await svc.resume.approve(req, responded_by="ceo")
        await _wait_until(lambda: store.load(run_id) and store.load(run_id).status == "done")

        assert tx.calls == [("notify__email", {"to": "ceo@co"})]  # ran exactly once
        assert store.load(run_id).final_output == "sent"
    finally:
        await svc.stop()


async def test_worker_tier_plain_run_completes_without_pause():
    """A run with no gated tool flows straight through the worker to done."""
    agent = _agent_def()
    agent.metadata["_governance"] = {}  # no approvals
    agent.tools = ["search__files"]
    registry = FakeRegistry(agent)
    kernel = Kernel(registry=registry)
    llm = FakeLLM([
        LLMResponse(text="", model="m", provider="anthropic", tokens_used=4,
                    tool_calls=[ToolCall(id="t1", name="search__files", input={"q": "x"})]),
        LLMResponse(text="found", model="m", provider="anthropic", tokens_used=2),
    ])
    tx = FakeTool()
    store = MemoryContinuationStore()
    svc = RuntimeService(kernel=kernel, llm_router=llm, tool_executor=tx, registry=registry,
                         store=store, ledger=InMemoryLedger(), queue=InMemoryRunnableQueue(), workers=1)
    await svc.start()
    try:
        run_id = await svc.enqueue_invoke(agent, "search")
        await _wait_until(lambda: store.load(run_id) and store.load(run_id).status == "done")
        assert store.load(run_id).final_output == "found"
        assert tx.calls == [("search__files", {"q": "x"})]
    finally:
        await svc.stop()
