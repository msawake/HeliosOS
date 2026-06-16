"""
Multi-approval suspend/resume — the "two gated tools in one turn" case.

Reproduces the dashboard chat bug: when one LLM turn emits two (or more) tool
calls that each need human approval (e.g. two ``jira_create_issue`` calls), the
run parks with BOTH pending. Approving them one at a time must:

  * execute each approved tool EXACTLY once (never re-run an already-approved
    call when a sibling is approved later),
  * surface the just-approved tool's result on the partial resume (so the chat
    shows progress instead of going silent until the last approval),
  * NOT re-prompt for the still-pending siblings that were already surfaced —
    the partial-resume outcome is flagged ``awaiting_remaining`` so the chat SSE
    translator skips re-emitting their ``hitl_request`` events (the bug: every
    partial resume re-listed the remaining approvals, so the operator was asked
    again and again and the chip list kept growing).

These run against the real :class:`StepEngine` with a fake kernel that gates by
tool name and a fake tool that counts executions.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.platform.llm_router import LLMResponse, ToolCall
from src.runtime import (
    MemoryContinuationStore,
    Resolution,
    ResolutionOutcome,
    RunStatus,
    StepEngine,
)


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat(self, llm_config, messages, tools=None):
        return self._responses.pop(0)


class FakeTool:
    """Counts executions per tool_use so a double-run is observable."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name, tool_input, agent_context=None):
        self.calls.append((name, dict(tool_input or {})))
        return {"created": tool_input.get("summary"), "ok": True}

    def count(self, summary: str) -> int:
        return sum(1 for _n, args in self.calls if args.get("summary") == summary)


class GatingKernel:
    """Returns ``ask_human`` for a tool until the call carries a capability
    token; ``allow`` otherwise. Mirrors how the real kernel flips ask_human ->
    allow once an approval token is present. Records every admission so a test
    can prove the kernel — not a bypass — admits the post-approval execution."""

    def __init__(self, gated: set[str]):
        self._gated = set(gated)
        # (object, capability_token, action) for every syscall.
        self.admissions: list[tuple[str, str | None, str]] = []

    def syscall(self, *, verb, subject, object, args, dispatcher=None):
        token = args.get("capability_token")
        gated = object in self._gated and not token
        action = "ask_human" if gated else "allow"
        self.admissions.append((object, token, action))
        if gated:
            return SimpleNamespace(
                action="ask_human",
                reason="needs human approval",
                details={"suspend_reason": "human_approval"},
            )
        return SimpleNamespace(action="allow", reason="", details={})

    def issue_capability(self, *, subject, target, verb, ttl_seconds=3600, metadata=None):
        return SimpleNamespace(id=f"tok::{target}")


def _engine(llm, tool, *, single_step=False):
    return StepEngine(
        llm_router=llm,
        kernel=GatingKernel({"jira_create_issue"}),
        store=MemoryContinuationStore(),
        single_step=single_step,
    )


def _two_create_calls():
    return LLMResponse(
        text="", model="m", provider="anthropic", tokens_used=10,
        tool_calls=[
            ToolCall(id="tu1", name="jira_create_issue",
                     input={"summary": "Add environments for forgeos"}),
            ToolCall(id="tu2", name="jira_create_issue",
                     input={"summary": "Treasury agents review"}),
        ],
    )


async def _suspend_two(engine, tool):
    return await engine.run(
        pid="jira-helper",
        system_prompt="You file Jira tickets.",
        user_prompt="Create two tasks",
        provider="anthropic",
        chat_model="claude-x",
        tools=[{"name": "jira_create_issue"}],
        tool_executor=tool,
    )


async def test_two_gated_calls_park_with_both_pending():
    tool = FakeTool()
    llm = FakeLLM([_two_create_calls(),
                   LLMResponse(text="Both created", model="m", provider="anthropic", tokens_used=3)])
    engine = _engine(llm, tool)

    outcome = await _suspend_two(engine, tool)

    assert outcome.status is RunStatus.SUSPENDED
    assert tool.calls == []  # nothing executed before approval
    pending_ids = sorted(p["tool_use_id"] for p in outcome.pending)
    assert pending_ids == ["tu1", "tu2"]


async def test_partial_resume_runs_one_tool_and_keeps_only_sibling_pending():
    tool = FakeTool()
    llm = FakeLLM([_two_create_calls(),
                   LLMResponse(text="Both created", model="m", provider="anthropic", tokens_used=3)])
    engine = _engine(llm, tool)
    suspended = await _suspend_two(engine, tool)
    cont_id = suspended.continuation_id

    # Approve the FIRST tool only.
    out1 = await engine.resume(
        Resolution(continuation_id=cont_id, tool_use_id="tu1",
                   outcome=ResolutionOutcome.ACCEPT, capability_token="tok1"),
        tool_executor=tool,
    )

    # Still parked, waiting on tu2 only.
    assert out1.status is RunStatus.SUSPENDED
    assert [p["tool_use_id"] for p in out1.pending] == ["tu2"]
    # The approved tool ran exactly once...
    assert tool.count("Add environments for forgeos") == 1
    assert tool.count("Treasury agents review") == 0
    # ...and its result is surfaced on the partial resume (chat shows progress).
    assert [e["name"] for e in out1.tool_events] == ["jira_create_issue"]
    assert out1.tool_events[0]["input"]["summary"] == "Add environments for forgeos"
    # The remaining approval was already surfaced — flag it so the chat layer
    # does NOT re-prompt for tu2 (the escalating-prompts bug).
    assert out1.awaiting_remaining is True


async def test_resolving_all_runs_each_tool_exactly_once_then_completes():
    tool = FakeTool()
    llm = FakeLLM([_two_create_calls(),
                   LLMResponse(text="Both created", model="m", provider="anthropic", tokens_used=3)])
    engine = _engine(llm, tool)
    suspended = await _suspend_two(engine, tool)
    cont_id = suspended.continuation_id

    await engine.resume(
        Resolution(continuation_id=cont_id, tool_use_id="tu1",
                   outcome=ResolutionOutcome.ACCEPT, capability_token="tok1"),
        tool_executor=tool,
    )
    out2 = await engine.resume(
        Resolution(continuation_id=cont_id, tool_use_id="tu2",
                   outcome=ResolutionOutcome.ACCEPT, capability_token="tok2"),
        tool_executor=tool,
    )

    assert out2.status is RunStatus.DONE
    assert out2.output == "Both created"
    assert not out2.awaiting_remaining
    # Each tool executed EXACTLY once across the whole run — no re-runs when the
    # sibling approval landed.
    assert tool.count("Add environments for forgeos") == 1
    assert tool.count("Treasury agents review") == 1
    assert len(tool.calls) == 2


async def test_double_approval_of_same_call_does_not_re_execute():
    """Approving the same request twice (e.g. a duplicate chip click) must be
    idempotent — the tool runs once, not twice."""
    tool = FakeTool()
    llm = FakeLLM([_two_create_calls(),
                   LLMResponse(text="Both created", model="m", provider="anthropic", tokens_used=3)])
    engine = _engine(llm, tool)
    suspended = await _suspend_two(engine, tool)
    cont_id = suspended.continuation_id

    res1 = Resolution(continuation_id=cont_id, tool_use_id="tu1",
                      outcome=ResolutionOutcome.ACCEPT, capability_token="tok1")
    await engine.resume(res1, tool_executor=tool)
    # Replay the very same approval.
    again = await engine.resume(res1, tool_executor=tool)

    assert again.status is RunStatus.SUSPENDED  # still waiting on tu2
    assert again.awaiting_remaining is True
    assert tool.count("Add environments for forgeos") == 1  # NOT re-run


async def test_approved_tool_is_admitted_by_kernel_with_capability_token():
    """The post-approval execution must go THROUGH the kernel (carrying the
    approval token that flips ask_human -> allow), not bypass it."""
    tool = FakeTool()
    llm = FakeLLM([_two_create_calls(),
                   LLMResponse(text="Both created", model="m", provider="anthropic", tokens_used=3)])
    kernel = GatingKernel({"jira_create_issue"})
    engine = StepEngine(llm_router=llm, kernel=kernel,
                        store=MemoryContinuationStore(), single_step=False)
    suspended = await _suspend_two(engine, tool)
    cont_id = suspended.continuation_id

    # Before approval: both admissions were gated (no token → ask_human).
    pre = [a for a in kernel.admissions if a[0] == "jira_create_issue"]
    assert len(pre) == 2 and all(tok is None and action == "ask_human" for _o, tok, action in pre)

    await engine.resume(
        Resolution(continuation_id=cont_id, tool_use_id="tu1",
                   outcome=ResolutionOutcome.ACCEPT, capability_token="cap-1"),
        tool_executor=tool,
    )

    # The approved execution went back through the kernel, this time admitted
    # (allow) because it carried the capability token.
    approved = [a for a in kernel.admissions if a[1] == "cap-1"]
    assert approved == [("jira_create_issue", "cap-1", "allow")]
    assert tool.calls == [("jira_create_issue", {"summary": "Add environments for forgeos"})]
