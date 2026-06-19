"""
Chat SSE translation — turn-level run results → the events the chat UI consumes.

Kept FastAPI-free (and import-light) so the translation logic is unit-testable
on its own: the dashboard chat stream/resume endpoints import these helpers, and
tests exercise them directly without standing up the whole app.

The chat UI's ``applyEvent`` consumes: ``text_delta``, ``tool_call``,
``tool_result``, ``hitl_request``, ``error``, ``done``.

Key invariant: a GATED tool call (awaiting human approval) is surfaced as an
``hitl_request`` approval card ONLY — never a premature ``tool_call``. A
``tool_call`` with no matching ``tool_result`` renders as a tool chip that spins
forever (the orphaned "tool loading" the operator sees stuck after approving).
The tool actually runs only after approval, and its real
``tool_call``/``tool_result`` pair arrives on the resume stream.
"""

from __future__ import annotations

from typing import Any


def _gated_approval_event(p: dict) -> dict[str, Any]:
    """The approval card for ONE gated tool call.

    Carries the tool name + args so the operator sees exactly what they're
    approving — without emitting a ``tool_call`` that would otherwise spin
    forever waiting on a ``tool_result`` that only arrives post-approval.
    Tolerates both the adapter's pending shape (``name``/``arguments``/
    ``external_ref``) and any pre-shaped (``tool``/``args``/``request_id``) dict.
    """
    tool = p.get("tool") or p.get("name") or "tool"
    args = p.get("args") or p.get("arguments") or {}
    return {
        "type": "hitl_request",
        "request_id": p.get("request_id") or p.get("external_ref"),
        "title": f"Approve {tool}?",
        "tool": tool,
        "args": args,
        "risk": "high",
    }


def agent_result_to_chat_events(result) -> list[dict]:
    """Translate an AgentResult (from the engine path) into chat SSE events.
    Turn-level (no token streaming):

      PAUSED    -> executed tool_events + one approval card per pending gated tool
      COMPLETED -> a single text_delta with the final output
      FAILED    -> error
    """
    events: list[dict] = []
    status = getattr(result.status, "value", str(result.status)) if result else "failed"
    meta = getattr(result, "metadata", None) or {}
    # Executed (non-gated) tool calls — surface the function calls, not just the
    # final text. Gated calls awaiting approval become approval cards below.
    for te in meta.get("tool_events") or []:
        tuid = te.get("tool_use_id")
        events.append({"type": "tool_call", "name": te.get("name", "tool"),
                       "input": te.get("input") or {}, "tool_use_id": tuid})
        events.append({"type": "tool_result", "name": te.get("name", "tool"),
                       "result": te.get("result"), "tool_use_id": tuid,
                       "is_error": te.get("is_error", False)})
    if status == "paused":
        for p in meta.get("pending") or []:
            events.append(_gated_approval_event(p))
    elif status == "failed":
        events.append({"type": "error", "error": result.error or "Run failed"})
    else:  # completed (or any terminal-with-output)
        if getattr(result, "output", None):
            events.append({"type": "text_delta", "content": result.output})
    events.append({
        "type": "done",
        "tokens_used": getattr(result, "tokens_used", 0) or 0,
        "text": getattr(result, "output", "") or "",
        "status": status,
    })
    return events


def run_outcome_to_chat_events(outcome) -> list[dict]:
    """Translate a runtime-v2 RunOutcome (from engine.resume) into chat SSE
    events. A resumed run may COMPLETE, FAIL, or SUSPEND again on a NEW gated
    tool (→ another approval card)."""
    from src.runtime import RunStatus

    events: list[dict] = []
    if outcome is None:
        return [
            {"type": "error", "error": "No suspended run found for this approval"},
            {"type": "done", "tokens_used": 0, "text": ""},
        ]
    for te in getattr(outcome, "tool_events", None) or []:
        tuid = te.get("tool_use_id")
        events.append({"type": "tool_call", "name": te.get("name", "tool"),
                       "input": te.get("input") or {}, "tool_use_id": tuid})
        events.append({"type": "tool_result", "name": te.get("name", "tool"),
                       "result": te.get("result"), "tool_use_id": tuid,
                       "is_error": te.get("is_error", False)})
    # A PARTIAL multi-approval resume parks again on the SAME sibling tool calls
    # the human was already asked about — re-emitting approval cards for them
    # would re-prompt for approvals already on screen (the escalating-prompts
    # bug). awaiting_remaining marks exactly that case: skip the re-prompt; the
    # still-pending chips remain live client-side.
    if outcome.status is RunStatus.SUSPENDED and not getattr(outcome, "awaiting_remaining", False):
        for p in outcome.pending or []:
            events.append(_gated_approval_event(p))
    elif outcome.status is RunStatus.FAILED:
        events.append({"type": "error", "error": outcome.error or "Run failed"})
    elif outcome.status is not RunStatus.SUSPENDED:  # DONE
        if getattr(outcome, "output", None):
            events.append({"type": "text_delta", "content": outcome.output})
    events.append({
        "type": "done",
        "tokens_used": getattr(outcome, "tokens_used", 0) or 0,
        "text": getattr(outcome, "output", "") or "",
        "status": getattr(outcome.status, "value", str(outcome.status)),
    })
    return events


__all__ = [
    "agent_result_to_chat_events",
    "run_outcome_to_chat_events",
]
