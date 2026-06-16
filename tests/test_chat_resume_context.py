"""
Chat suspend/resume UX regressions:

1. Orphaned "tool loading" chip — a gated tool was streamed as a `tool_call`
   (running tool chip) AND an approval card. The tool only runs after approval,
   so that running chip never got a `tool_result` and spun forever, while a
   second chip (from the resume stream) showed the real result. Fix: a gated
   tool is surfaced as an approval card ONLY; the real tool_call/tool_result
   arrive on resume. The card carries the tool + args so the operator still sees
   what they're approving.

2. Lost context after approval — each chat turn is its own executor.invoke, and
   the prior conversation is re-seeded from the executor session store. A PAUSED
   (gated) turn used to record an EMPTY assistant turn, and the resume (which
   bypasses executor.invoke) never wrote the real answer back — so the next turn
   saw blank assistant turns and forgot what the agent had just done (e.g. a
   follow-up "delete it" couldn't resolve what "it" was). Fix: the paused invoke
   records only the user turn; record_resumed_turn appends the assistant turn
   once the run resumes to completion.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from src.dashboard.chat_events import (
    agent_result_to_chat_events,
    run_outcome_to_chat_events,
)
from src.runtime import RunOutcome, RunStatus
from stacks.base import AgentResult, AgentStatus


# ---------------------------------------------------------------------------
# 1. Gated tool → approval card only (no orphaned running chip)
# ---------------------------------------------------------------------------

def _names(events, etype):
    return [e.get("name") for e in events if e["type"] == etype]


def test_paused_result_gated_tool_is_card_only_not_running_chip():
    # Mirrors the "add a list of animals" turn: jira_get_issue ran (not gated),
    # jira_update_issue is gated and awaiting approval.
    result = AgentResult(
        agent_id="jira-helper", status=AgentStatus.PAUSED, output="",
        metadata={
            "continuation_id": "c1",
            "tool_events": [
                {"name": "jira_get_issue", "input": {"issue_key": "PR12148-457"},
                 "result": {"ok": True}},
            ],
            "pending": [
                {"name": "jira_update_issue", "arguments": {"issue_key": "PR12148-457"},
                 "external_ref": "req-1", "tool_use_id": "tu1"},
            ],
        },
    )
    events = agent_result_to_chat_events(result)

    # The executed, non-gated tool surfaces as a real call + result pair.
    assert _names(events, "tool_call") == ["jira_get_issue"]
    assert _names(events, "tool_result") == ["jira_get_issue"]
    # The gated tool produces NO tool_call (which would orphan a forever-running
    # chip) — only an approval card carrying the tool + args.
    hitl = [e for e in events if e["type"] == "hitl_request"]
    assert len(hitl) == 1
    assert hitl[0]["request_id"] == "req-1"
    assert hitl[0]["tool"] == "jira_update_issue"
    assert hitl[0]["args"] == {"issue_key": "PR12148-457"}


def test_run_outcome_suspended_gated_tool_is_card_only():
    outcome = RunOutcome(
        status=RunStatus.SUSPENDED, continuation_id="c1",
        pending=[{"name": "jira_add_comment", "arguments": {"body": "hi"},
                  "external_ref": "req-2"}],
    )
    events = run_outcome_to_chat_events(outcome)
    assert _names(events, "tool_call") == []  # no premature running chip
    hitl = [e for e in events if e["type"] == "hitl_request"]
    assert len(hitl) == 1 and hitl[0]["tool"] == "jira_add_comment"
    assert hitl[0]["args"] == {"body": "hi"}


def test_resume_surfaces_approved_tool_and_does_not_reprompt_remaining():
    # Partial multi-approval resume: the approved tool executed (tool_events) and
    # a sibling is still pending — but we must NOT re-prompt for the sibling.
    outcome = RunOutcome(
        status=RunStatus.SUSPENDED, continuation_id="c1",
        tool_events=[{"name": "jira_update_issue", "input": {"issue_key": "PR12148-457"},
                      "result": {"ok": True}}],
        pending=[{"name": "jira_add_comment", "arguments": {}, "external_ref": "req-2"}],
        awaiting_remaining=True,
    )
    events = run_outcome_to_chat_events(outcome)
    # The just-approved tool's real call + result are shown (progress, not silence).
    assert _names(events, "tool_call") == ["jira_update_issue"]
    assert _names(events, "tool_result") == ["jira_update_issue"]
    # ...and the still-pending sibling is NOT re-prompted.
    assert not any(e["type"] == "hitl_request" for e in events)


def test_resume_done_emits_final_text():
    outcome = RunOutcome(status=RunStatus.DONE, continuation_id="c1",
                         output="Comment added (ID 335145).")
    events = run_outcome_to_chat_events(outcome)
    assert {"type": "text_delta", "content": "Comment added (ID 335145)."} in events
    assert not any(e["type"] == "hitl_request" for e in events)


# ---------------------------------------------------------------------------
# 2. Context survives a suspend → approve → resume → next-turn cycle
# ---------------------------------------------------------------------------

def _executor_with_stub_adapter(invoke_fn):
    from src.platform.executor import PlatformExecutor
    from src.platform.registry import AgentRegistry
    from src.platform.scheduler import SchedulerEngine
    from src.platform.event_bus import EventBus
    from src.core.session_store import InMemorySessionStore
    from stacks.base import AgentDefinition, ExecutionType, OwnershipType

    agent_def = AgentDefinition(
        agent_id="jira-helper", name="jira-helper", stack="forgeos",
        execution_type=ExecutionType.REFLEX, ownership=OwnershipType.SHARED,
        namespace="default", description="files tickets", tools=[],
        llm_config={"chat_model": "claude-x", "provider": "anthropic"},
        system_prompt="You file Jira tickets.", metadata={},
    )
    registry = AgentRegistry()
    registry.register(agent_def)

    adapter = type("StubAdapter", (), {})()
    adapter.stack_name = "forgeos"
    adapter.invoke = invoke_fn
    adapter.create_agent = AsyncMock()
    adapter.stop = AsyncMock()
    adapter.start_loop = AsyncMock()
    adapter.scaffold_files = lambda d: {}

    executor = PlatformExecutor(
        registry=registry, scheduler=SchedulerEngine(), event_bus=EventBus(),
    )
    executor.register_adapter(adapter)
    executor._register_process(agent_def)
    executor._session_store = InMemorySessionStore()
    return executor


async def test_resumed_turn_is_persisted_so_next_turn_keeps_context():
    seen_histories: list[list[dict]] = []
    statuses = iter([AgentStatus.PAUSED, AgentStatus.COMPLETED])

    async def fake_invoke(agent_id, prompt, context=None, history=None):
        seen_histories.append(list(history or []))
        st = next(statuses)
        return AgentResult(agent_id=agent_id, status=st,
                           output="" if st is AgentStatus.PAUSED else "Deleted comment 335145.")

    executor = _executor_with_stub_adapter(fake_invoke)
    sid = "sess-1"

    # Turn 1 gates on add_comment → parks. Only the USER turn is recorded; the
    # empty placeholder assistant turn is NOT written.
    r1 = await executor.invoke("jira-helper", "Add a comment to PR12148-457", session_id=sid)
    assert r1.status is AgentStatus.PAUSED
    sess = executor._session_store.get(sid)
    assert [m["role"] for m in sess.messages] == ["user"]

    # Human approves; the run resumes to completion → backfill the assistant turn.
    executor.record_resumed_turn(sid, "Comment added (ID 335145).")
    sess = executor._session_store.get(sid)
    assert [m["role"] for m in sess.messages] == ["user", "assistant"]
    assert "335145" in sess.messages[-1]["content"]

    # Turn 2 "Delete it" — the agent MUST see the prior comment in its history.
    await executor.invoke("jira-helper", "Delete it", session_id=sid)
    hist2 = seen_histories[-1]
    assert {"role": "assistant", "content": "Comment added (ID 335145)."} in hist2
    # No blank assistant turn leaked into history, and the first user turn is not
    # duplicated (regression guard for the InMemory double-append).
    assert all(m["content"] for m in hist2)
    assert sum(1 for m in hist2 if m["content"] == "Add a comment to PR12148-457") == 1


async def test_completed_turn_records_user_and_assistant_without_duplication():
    seen_histories: list[list[dict]] = []

    async def fake_invoke(agent_id, prompt, context=None, history=None):
        seen_histories.append(list(history or []))
        return AgentResult(agent_id=agent_id, status=AgentStatus.COMPLETED, output=f"ack: {prompt}")

    executor = _executor_with_stub_adapter(fake_invoke)
    sid = "sess-2"

    await executor.invoke("jira-helper", "first", session_id=sid)
    await executor.invoke("jira-helper", "second", session_id=sid)

    sess = executor._session_store.get(sid)
    # Exactly one user+assistant pair per turn — no double-append.
    assert [(m["role"], m["content"]) for m in sess.messages] == [
        ("user", "first"),
        ("assistant", "ack: first"),
        ("user", "second"),
        ("assistant", "ack: second"),
    ]
    # Turn 2 saw turn 1's full exchange as its history.
    assert seen_histories[-1] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ack: first"},
    ]
