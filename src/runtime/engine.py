"""
StepEngine — the suspendable, resumable agentic loop.

Replaces ``run_agentic_loop``. The loop is identical in spirit (LLM ->
tool_use -> execute -> tool_result -> LLM) with two differences:

1. **Every tool call is admitted by ``kernel.syscall``.** The kernel returns
   ``allow`` / ``deny`` / ``rate_limit`` / ``mask`` / ``ask_human``. On
   ``ask_human`` the engine does not run the tool — it parks.

2. **Parking is durable.** Instead of injecting an error tool_result and
   plowing on (the old behaviour), the engine persists a
   :class:`~src.runtime.continuation.Continuation` capturing the full message
   history and the pending tool calls, transitions the process to
   ``AWAITING_HUMAN`` / ``AWAITING_EXTERNAL``, and returns a ``suspended``
   outcome. The caller (a worker) is freed — nothing blocks waiting for the
   human.

On :meth:`resume`, the gated tool executes through the *same* syscall path
(the approval capability token flips the kernel's capability stage from
``ask_human`` to ``allow``), its result is injected into the exact
``tool_use`` slot, and the loop continues with full context.

Admission is synchronous (the kernel pipeline is sync); tool execution is
async and runs in the engine. The engine therefore calls ``kernel.syscall``
with no dispatcher (pure admission) and executes the tool itself on ``allow``,
committing any budget ticket afterwards.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.runtime.continuation import Continuation, ToolCallRecord
from src.runtime.shaping import (
    provider_kind,
    shape_assistant_turn,
    shape_initial,
    shape_tool_results,
)
from src.runtime.signals import Resolution, ResolutionOutcome, Suspend, SuspendReason

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 300


class RunStatus(str, Enum):
    DONE = "done"
    SUSPENDED = "suspended"
    FAILED = "failed"
    MAX_TURNS = "max_turns"
    # Single-step mode only: the turn finished with tool results appended and
    # the run should continue on a FRESH worker. The worker re-enqueues the
    # continuation (one LLM turn == one runnable Redis task).
    CONTINUE = "continue"


@dataclass
class RunOutcome:
    """The result of a (slice of a) run."""

    status: RunStatus
    continuation_id: str
    output: str = ""
    error: str | None = None
    suspend_reason: str | None = None
    pending: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    # Run rollup, surfaced so the adapter can populate the agent_runs row
    # (otherwise tool_calls / input+output token split / model show as 0/null).
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    model: str | None = None
    # Executed tool calls in THIS run slice (name/input/result/is_error), so a
    # caller (e.g. the chat stream) can render the function calls, not just the
    # final text. Gated calls awaiting approval are in `pending`, not here.
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    # True only for a PARTIAL multi-approval resume: this resolution executed,
    # but sibling tool calls from the same turn are still parked. Those siblings
    # are already in `pending`, but they were ALSO surfaced to the human when the
    # run first parked — so the chat layer must NOT re-prompt for them (doing so
    # re-asks for the same approvals on every partial resume). See StepEngine.resume.
    awaiting_remaining: bool = False

    @property
    def suspended(self) -> bool:
        return self.status is RunStatus.SUSPENDED

    @property
    def done(self) -> bool:
        return self.status is RunStatus.DONE

    @property
    def should_continue(self) -> bool:
        return self.status is RunStatus.CONTINUE


class _Suspended:
    """Internal sentinel returned by ``_dispatch`` so a suspend does not
    propagate through ``asyncio.gather`` and cancel sibling tool calls.

    The public signal type is :class:`Suspend` (a BaseException tools may
    raise); the engine converts it to this sentinel before gather sees it.
    """

    __slots__ = ("signal",)

    def __init__(self, signal: Suspend) -> None:
        self.signal = signal


class StepEngine:
    """Drives runs and resumes against a :class:`ContinuationStore`."""

    def __init__(
        self,
        *,
        llm_router,
        kernel,
        store,
        process_table=None,
        gateway=None,
        max_turns: int = MAX_TOOL_TURNS,
        single_step: bool = False,
    ) -> None:
        self._llm = llm_router
        self._kernel = kernel
        self._store = store
        self._process_table = process_table
        # Optional A2H gateway: open_request(...) -> request_id. When absent the
        # engine mints a synthetic ref so suspend/resume still round-trips.
        self._gateway = gateway
        self._max_turns = max_turns
        # Per-turn worker mode: when True, ``drive``/``resume`` advance exactly
        # ONE LLM turn and return ``CONTINUE`` so the worker re-enqueues the
        # continuation for a fresh worker (the model the operator watches:
        # user -> query -> worker -> one LLM call -> tool via kernel -> new
        # worker with history + tool result, or final response ends the run).
        # The inline adapter engine keeps single_step=False (full internal loop).
        self._single_step = single_step

    # -- public API --------------------------------------------------------

    def create_continuation(
        self,
        *,
        pid: str,
        system_prompt: str,
        user_prompt: str,
        provider: str,
        chat_model: str,
        endpoint: str | None = None,
        api_key_ref: str | None = None,
        tools: list[dict] | None = None,
        session_id: str | None = None,
        history: list[dict] | None = None,
        context: dict | None = None,
        goal: str | None = None,
        tenant_id: str = "default",
        namespace: str = "default",
        run_id: str | None = None,
        source: str = "manual",
        generation: int = 1,
        max_turns: int | None = None,
        status: str = "running",
    ) -> Continuation:
        """Build + persist a fresh continuation WITHOUT driving it.

        Triggers / the enqueuer create the continuation here, then enqueue its
        id; a worker later loads and :meth:`drive`s it. :meth:`run` is the
        synchronous shorthand (create + drive in one call).
        """
        messages = shape_initial(system_prompt, history, user_prompt, context, goal)
        cont = Continuation(
            pid=pid,
            generation=generation,
            run_id=run_id,
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=(context or {}).get("user_id") or "default",
            namespace=namespace,
            messages=messages,
            provider=provider,
            chat_model=chat_model,
            endpoint=endpoint,
            api_key_ref=api_key_ref,
            tool_definitions=tools,
            step_index=0,
            max_turns=max_turns or self._max_turns,
            goal=goal,
            source=source,
            status=status,
        )
        self._store.save(cont)
        return cont

    async def run(self, *, tool_executor=None, agent_context: dict | None = None, **kwargs) -> RunOutcome:
        """Start a fresh run synchronously (create + drive). Returns when the
        run completes or suspends. Accepts the same kwargs as
        :meth:`create_continuation`."""
        cont = self.create_continuation(**kwargs)
        return await self._drive(cont, tool_executor, agent_context)

    async def drive(
        self,
        continuation_id: str,
        *,
        tool_executor=None,
        agent_context: dict | None = None,
    ) -> RunOutcome:
        """Load a persisted continuation and drive it from its current step.

        Used by the worker tier: a trigger creates the continuation and
        enqueues its id; the worker claims it and calls this.
        """
        cont = self._store.load(continuation_id)
        if cont is None:
            return RunOutcome(RunStatus.FAILED, continuation_id, error="continuation not found")
        if cont.status in ("done", "failed"):
            status = RunStatus.DONE if cont.status == "done" else RunStatus.FAILED
            return RunOutcome(status, continuation_id, output=cont.final_output, error=cont.last_error)
        if cont.status == "suspended":
            # Nothing to drive until a resolution arrives.
            return self._suspended_outcome(cont)
        cont.status = "running"
        self._store.save(cont)
        self._transition(cont, "running")
        return await self._drive_or_step(cont, tool_executor, agent_context)

    async def resume(
        self,
        resolution: Resolution,
        *,
        tool_executor=None,
        agent_context: dict | None = None,
    ) -> RunOutcome:
        """Resume a suspended continuation with a delivered resolution."""
        cont = self._store.load(resolution.continuation_id)
        if cont is None:
            return RunOutcome(RunStatus.FAILED, resolution.continuation_id,
                              error="continuation not found")
        logger.info("[resume] cont=%s status=%s outcome=%s token=%s tool_use=%s",
                    cont.continuation_id, cont.status, resolution.outcome,
                    "yes" if resolution.capability_token else "NO", resolution.tool_use_id)
        if cont.status != "suspended":
            # Already resumed / done / stale — idempotent no-op.
            logger.info("[resume] cont=%s SKIP — not suspended (status=%s)",
                        cont.continuation_id, cont.status)
            return RunOutcome(RunStatus.FAILED, cont.continuation_id,
                              error=f"continuation not suspended (status={cont.status})")

        cont.status = "resuming"
        self._store.save(cont)

        rec = cont.pending_by_id(resolution.tool_use_id)
        if rec is None:
            return RunOutcome(RunStatus.FAILED, cont.continuation_id,
                              error=f"no pending tool_use {resolution.tool_use_id}")

        logger.info("[resume] cont=%s rec.status=%s -> %s", cont.continuation_id, rec.status,
                    "apply_resolution" if rec.status != "executed" else "SKIP (already executed)")
        # Idempotent: if this call already executed (e.g. a duplicate approval /
        # double-clicked chip), do NOT run the tool again — just settle below.
        if rec.status != "executed":
            await self._apply_resolution(cont, rec, resolution, tool_executor, agent_context)

        # The just-resolved tool ran (ACCEPT/RESULT) or was rejected in
        # _apply_resolution, OUTSIDE the turn loop — capture it once here so the
        # resume stream shows this function call + result, not just follow-up
        # text. Each partial resume surfaces only the ONE call it resolved; a
        # sibling resolved by an earlier resume was already surfaced by that
        # resume, so we never double-emit it.
        just_resolved = (
            {"name": rec.name, "input": rec.arguments, "result": rec.result,
             "is_error": rec.result_is_error}
            if rec.status in ("executed", "rejected") else None
        )

        # Multi-approval: stay parked until every pending call is resolved. The
        # siblings still pending were already surfaced to the human when the run
        # first parked — flag the outcome (awaiting_remaining) so the chat layer
        # does NOT re-prompt for them. Surface the call we just resolved so the
        # operator sees progress instead of silence until the final approval.
        if any(r.status == "pending" for r in cont.pending_calls):
            cont.status = "suspended"
            self._store.save(cont)
            out = self._suspended_outcome(cont)
            out.awaiting_remaining = True
            if just_resolved is not None:
                out.tool_events = [just_resolved]
            return out

        # All resolved -> inject results into the message history and continue.
        kind = provider_kind(cont.provider, cont.chat_model)
        shape_tool_results(cont.messages, cont.pending_calls, kind)
        resolved_events = [just_resolved] if just_resolved is not None else []
        cont.pending_calls = []
        cont.suspend_reason = None
        cont.status = "running"
        cont.step_index += 1
        self._store.save(cont)
        self._transition(cont, "running")
        outcome = await self._drive_or_step(cont, tool_executor, agent_context)
        if outcome is not None:
            outcome.tool_events = resolved_events + list(outcome.tool_events or [])
        return outcome

    # -- core loop ---------------------------------------------------------

    async def _drive_or_step(self, cont: Continuation, tool_executor, agent_context) -> RunOutcome:
        """Drive the whole loop (inline) or exactly one turn (worker tier)."""
        if self._single_step:
            return await self._step(cont, tool_executor, agent_context)
        return await self._drive(cont, tool_executor, agent_context)

    async def _drive(self, cont: Continuation, tool_executor, agent_context) -> RunOutcome:
        """Inline mode: drive the whole loop in one worker until the run
        completes, suspends, fails, or hits max turns."""
        from stacks.base import LLMConfig

        llm_config = LLMConfig(chat_model=cont.chat_model, provider=cont.provider,
                               endpoint=cont.endpoint, api_key_ref=cont.api_key_ref)
        kind = provider_kind(cont.provider, cont.chat_model)
        tools = cont.tool_definitions or None
        tokens = 0

        tool_events: list[dict] = []
        turn = cont.step_index
        while turn < cont.max_turns:
            outcome, tokens = await self._run_one_turn(
                cont, llm_config, kind, tools, turn, tokens, tool_executor, agent_context,
                tool_events=tool_events,
            )
            if outcome is not None:
                outcome.tool_events = tool_events
                return outcome
            turn += 1

        return self._max_turns_reached(cont, tokens)

    async def _step(self, cont: Continuation, tool_executor, agent_context) -> RunOutcome:
        """Worker-tier mode: drive exactly ONE turn, then return a terminal
        outcome (DONE/SUSPENDED/FAILED/MAX_TURNS — the process ends or parks) or
        ``CONTINUE`` to tell the worker to re-enqueue the continuation so a fresh
        worker runs the next turn. This is what makes "one LLM turn == one
        runnable Redis task" hold: the agent never knows whether the previous
        tool was executed inline or took three hours behind a human approval —
        it just resumes from the persisted history + tool result."""
        from stacks.base import LLMConfig

        turn = cont.step_index
        if turn >= cont.max_turns:
            return self._max_turns_reached(cont, 0)

        llm_config = LLMConfig(chat_model=cont.chat_model, provider=cont.provider,
                               endpoint=cont.endpoint, api_key_ref=cont.api_key_ref)
        kind = provider_kind(cont.provider, cont.chat_model)
        tools = cont.tool_definitions or None

        tool_events: list[dict] = []
        outcome, tokens = await self._run_one_turn(
            cont, llm_config, kind, tools, turn, 0, tool_executor, agent_context,
            tool_events=tool_events,
        )
        if outcome is not None:
            outcome.tool_events = tool_events
            return outcome  # DONE / SUSPENDED / FAILED — end or park here.

        # The turn appended tool results (this is also the text+tool_call edge
        # case: has_tool_calls=True keeps us here, never at _complete). Advance
        # the step index, persist, and hand the next turn to a fresh worker.
        cont.step_index = turn + 1
        cont.status = "running"
        self._store.save(cont)
        if turn + 1 >= cont.max_turns:
            return self._max_turns_reached(cont, tokens)
        return RunOutcome(RunStatus.CONTINUE, cont.continuation_id, tokens_used=tokens)

    async def _run_one_turn(
        self,
        cont: Continuation,
        llm_config,
        kind: str,
        tools,
        turn: int,
        tokens_in: int,
        tool_executor,
        agent_context,
        tool_events: list[dict] | None = None,
    ) -> tuple[RunOutcome | None, int]:
        """Run a single LLM turn (one ``chat`` call → admit+dispatch any tool
        calls through the kernel → append results). Returns
        ``(terminal_outcome, tokens)`` where ``terminal_outcome`` is None when
        the turn finished without a terminal result (the caller continues).

        This is the ONE place the LLM→tool_use→tool_result step lives, shared by
        :meth:`_drive` (inline loop) and :meth:`_step` (per-turn worker mode), so
        the edge case — an assistant turn carrying BOTH text and a tool call
        must dispatch the tool and continue, never finalize at the text — cannot
        diverge between the two modes (``response.has_tool_calls`` is True
        whenever a tool call exists, regardless of accompanying text)."""
        cont.step_index = turn
        response = await self._llm.chat(llm_config, cont.messages, tools=tools)

        if response.error:
            return self._fail(cont, response.error), tokens_in

        tokens = tokens_in + response.tokens_used
        self._account(cont, response)

        if not response.has_tool_calls:
            return self._complete(cont, response.text, tokens), tokens

        # 1. Record the assistant turn (with native tool-call ids).
        shape_assistant_turn(cont.messages, response, kind)

        # 2. Build records + dispatch every tool through the kernel.
        from src.platform.agentic_loop import _resolve_tool_name
        records = [
            ToolCallRecord(
                tool_use_id=tc.id,
                name=_resolve_tool_name(tc.name, cont.tool_definitions),
                arguments=tc.input,
            )
            for tc in (response.tool_calls or [])
        ]
        # Accumulate the tool-call count across turns for the run rollup.
        cont.resource_usage["tool_calls"] = (
            cont.resource_usage.get("tool_calls", 0) + len(records)
        )
        results = await asyncio.gather(
            *(self._dispatch(cont, rec, tool_executor, agent_context) for rec in records),
            return_exceptions=True,
        )

        # 3. Resolve outcomes; collect any suspensions.
        suspended_any = False
        for rec, res in zip(records, results):
            if isinstance(res, _Suspended):
                rec.status = "pending"
                rec.suspend_reason = res.signal.reason
                rec.external_ref = res.signal.external_ref
                suspended_any = True
            elif isinstance(res, BaseException):
                rec.status = "executed"
                rec.result = {"error": str(res)}
                rec.result_is_error = True
            else:
                rec.status = "executed"
                rec.result = res
            if rec.status == "executed" and tool_events is not None:
                tool_events.append({
                    "tool_use_id": rec.tool_use_id,
                    "name": rec.name,
                    "input": rec.arguments,
                    "result": rec.result,
                    "is_error": rec.result_is_error,
                })

        if suspended_any:
            return await self._suspend(cont, records, tokens), tokens

        # 4. No suspension — append tool results; the caller continues.
        shape_tool_results(cont.messages, records, kind)
        self._store.save(cont)

        # Cooperative preemption at the tool boundary (reuses kernel signals).
        if self._kernel is not None and hasattr(self._kernel, "check_signals"):
            if "SIGTERM" in (self._kernel.check_signals(cont.pid) or []):
                return self._complete(cont, response.text or "", tokens), tokens

        return None, tokens

    # -- dispatch ----------------------------------------------------------

    async def _dispatch(
        self, cont: Continuation, rec: ToolCallRecord, tool_executor, agent_context,
    ) -> Any:
        """Admit one tool via the kernel, then execute it (or signal suspend).

        Returns the tool result, or a :class:`_Suspended` sentinel when the
        kernel says ``ask_human`` (or a tool raises :class:`Suspend`).
        """
        try:
            decision = self._admit(cont, rec.name, rec.arguments,
                                   capability_token=rec.capability_token)
        except Suspend as sig:  # a tool/stage may raise it directly
            return _Suspended(sig)

        action = getattr(decision, "action", "allow")

        if action == "ask_human":
            sig = self._open_suspension(cont, rec, decision)
            return _Suspended(sig)

        if action in ("deny", "rate_limit"):
            reason = getattr(decision, "reason", action)
            return self._error_result(f"{action}: {reason}")

        if action == "mask":
            details = getattr(decision, "details", {}) or {}
            return details.get("masked_payload", {"masked": True})

        # allow -> execute the tool, then settle the budget ticket.
        ticket = (getattr(decision, "details", {}) or {}).get("ticket")
        try:
            result = await self._execute_tool(rec.name, rec.arguments, tool_executor, agent_context)
        except Suspend as sig:  # tool decided it must wait (external_wait)
            self._release_ticket(ticket)
            return _Suspended(sig)
        finally:
            self._commit_ticket(ticket)
        return result

    async def _dispatch_authorized(
        self, cont: Continuation, rec: ToolCallRecord, tool_executor, agent_context,
    ) -> Any:
        """Re-dispatch a previously gated tool, now carrying its approval token.

        Goes through the same syscall path; the token short-circuits the
        capability stage to ``allow``. Never re-suspends on ``ask_human`` (a
        present-but-rejected token would, so we surface that as an error).
        """
        decision = self._admit(cont, rec.name, rec.arguments,
                               capability_token=rec.capability_token)
        action = getattr(decision, "action", "allow")
        if action == "ask_human":
            return self._error_result(
                "approval token did not authorize the gated tool (expired or mismatched)"
            )
        if action in ("deny", "rate_limit"):
            return self._error_result(f"{action}: {getattr(decision, 'reason', action)}")
        ticket = (getattr(decision, "details", {}) or {}).get("ticket")
        try:
            # Pass the approval token through to the tool executor so its OWN
            # kernel gate (the syscall pipeline it runs independently) also
            # short-circuits — otherwise it re-gates the approved tool back to
            # ask_human and the run can never actually send/execute it.
            return await self._execute_tool(
                rec.name, rec.arguments, tool_executor, agent_context,
                capability_token=rec.capability_token,
            )
        finally:
            self._commit_ticket(ticket)

    def _admit(self, cont: Continuation, tool_name: str, tool_input: dict,
               *, capability_token: str | None):
        """Run the kernel admission pipeline for one tool call (no dispatch)."""
        args: dict[str, Any] = {
            "tool_input": tool_input,
            "estimated_cost_usd": 0.0,
            "tenant_id": cont.tenant_id,
        }
        if capability_token:
            args["capability_token"] = capability_token
        return self._kernel.syscall(
            verb="tool.call",
            subject=cont.pid,
            object=tool_name,
            args=args,
            dispatcher=None,
        )

    async def _execute_tool(self, name: str, tool_input: dict, tool_executor, agent_context,
                            *, capability_token: str | None = None) -> Any:
        # Reuse the hardened retry/timeout executor from the legacy loop. When a
        # capability token is present (resumed approved tool), thread it through
        # the agent_context so the tool executor's own kernel gate honours it.
        from src.platform.agentic_loop import _execute_tool as _legacy_execute_tool

        # The engine already ran the full admission pipeline (capability + quota
        # reservation) in ``_admit`` just above. Mark the context so the tool
        # executor's OWN kernel gate does NOT re-admit — re-gating here is not
        # only redundant kernel work, it reserves a SECOND budget ticket for the
        # same call (double-counting quota). The legacy inline loop (no engine
        # pre-admission) never sets this flag, so it still admits normally.
        ctx = {**(agent_context or {}), "_kernel_admitted": True}
        if capability_token:
            ctx["capability_token"] = capability_token
        return await _legacy_execute_tool(name, tool_input, tool_executor, ctx)

    # -- suspend / resume helpers -----------------------------------------

    def _open_suspension(self, cont: Continuation, rec: ToolCallRecord, decision) -> Suspend:
        """Translate an ``ask_human`` decision into a Suspend, opening an A2H
        request (via the gateway when wired, else a synthetic ref)."""
        details = getattr(decision, "details", {}) or {}
        reason = details.get("suspend_reason", SuspendReason.HUMAN_APPROVAL)
        captured = details.get("captured_action") or {
            "verb": "tool.call",
            "tool_name": rec.name,
            "tool_input": rec.arguments,
        }
        request_id = details.get("request_id")
        if request_id is None and self._gateway is not None and hasattr(self._gateway, "open_request"):
            try:
                request_id = self._gateway.open_request(
                    continuation_id=cont.continuation_id,
                    pid=cont.pid,
                    tenant_id=cont.tenant_id,
                    captured_action=captured,
                    approvers=details.get("approvers") or [],
                    sla_hours=details.get("sla_hours"),
                    reason=getattr(decision, "reason", ""),
                )
            except Exception:  # pragma: no cover - defensive
                logger.exception("A2H gateway.open_request failed; using synthetic ref")
        if request_id is None:
            request_id = f"req_{uuid.uuid4().hex[:12]}"
        rec.suspend_reason = reason
        rec.external_ref = request_id
        return Suspend(reason=reason, tool_use_id=rec.tool_use_id, external_ref=request_id,
                       detail={"captured_action": captured})

    async def _apply_resolution(
        self, cont: Continuation, rec: ToolCallRecord, resolution: Resolution,
        tool_executor, agent_context,
    ) -> None:
        if resolution.outcome == ResolutionOutcome.REJECT:
            rec.status = "rejected"
            rec.result = {"error": "Human rejected this action.", "rejected": True}
            rec.result_is_error = True
        elif resolution.outcome == ResolutionOutcome.RESULT:
            rec.status = "executed"
            rec.result = resolution.result_payload
        else:  # ACCEPT
            rec.capability_token = resolution.capability_token
            result = await self._dispatch_authorized(cont, rec, tool_executor, agent_context)
            rec.status = "executed"
            rec.result = result
            if isinstance(result, dict) and result.get("error"):
                rec.result_is_error = True
        self._store.save(cont)

    async def _suspend(self, cont: Continuation, records: list[ToolCallRecord],
                       tokens: int) -> RunOutcome:
        cont.pending_calls = records
        cont.status = "suspended"
        # First pending reason drives the phase; mixed reasons are allowed.
        first_pending = next((r for r in records if r.status == "pending"), None)
        cont.suspend_reason = first_pending.suspend_reason if first_pending else SuspendReason.HUMAN_APPROVAL
        self._store.save(cont)
        for rec in records:
            if rec.external_ref:
                self._store.index_ref(rec.external_ref, cont.continuation_id)
        self._transition(cont, "awaiting")
        out = self._suspended_outcome(cont)
        out.tokens_used = tokens
        return out

    def _suspended_outcome(self, cont: Continuation) -> RunOutcome:
        return RunOutcome(
            status=RunStatus.SUSPENDED,
            continuation_id=cont.continuation_id,
            suspend_reason=cont.suspend_reason,
            pending=[
                {
                    "tool_use_id": r.tool_use_id,
                    "name": r.name,
                    "arguments": r.arguments,
                    "suspend_reason": r.suspend_reason,
                    "external_ref": r.external_ref,
                }
                for r in cont.pending_calls
                if r.status == "pending"
            ],
        )

    # -- terminal helpers --------------------------------------------------

    def _complete(self, cont: Continuation, final_text: str, tokens: int) -> RunOutcome:
        text = final_text or ""
        if cont.goal and re.search(r"^\[GOAL_COMPLETE\]$", text, re.MULTILINE):
            text = re.sub(r"\n?\[GOAL_COMPLETE\]\n?", "", text).strip()
        cont.status = "done"
        cont.final_output = text
        self._store.save(cont)
        return self._with_rollup(
            RunOutcome(RunStatus.DONE, cont.continuation_id, output=text, tokens_used=tokens),
            cont,
        )

    def _fail(self, cont: Continuation, error: str) -> RunOutcome:
        cont.status = "failed"
        cont.last_error = error
        self._store.save(cont)
        self._transition(cont, "failed")
        return RunOutcome(RunStatus.FAILED, cont.continuation_id, error=error)

    def _max_turns_reached(self, cont: Continuation, tokens: int) -> RunOutcome:
        cont.status = "done"
        cont.final_output = "[max tool turns reached]"
        self._store.save(cont)
        return self._with_rollup(
            RunOutcome(RunStatus.MAX_TURNS, cont.continuation_id,
                       output=cont.final_output, tokens_used=tokens),
            cont,
        )

    @staticmethod
    def _with_rollup(out: RunOutcome, cont: Continuation) -> RunOutcome:
        """Attach the per-run rollup (tool calls, token split, model) gathered on
        the continuation so the adapter can persist it on the agent_runs row."""
        usage = cont.resource_usage or {}
        out.input_tokens = int(usage.get("tokens_in", 0) or 0)
        out.output_tokens = int(usage.get("tokens_out", 0) or 0)
        out.tool_calls = int(usage.get("tool_calls", 0) or 0)
        out.model = cont.chat_model or None
        return out

    # -- side effects ------------------------------------------------------

    @staticmethod
    def _error_result(msg: str) -> dict[str, Any]:
        return {"error": msg}

    def _account(self, cont: Continuation, response) -> None:
        usage = cont.resource_usage
        usage["tokens_in"] = usage.get("tokens_in", 0) + (response.input_tokens or response.tokens_used or 0)
        usage["tokens_out"] = usage.get("tokens_out", 0) + (response.output_tokens or 0)
        if self._process_table is not None:
            try:
                self._process_table.record_usage(
                    cont.pid,
                    tokens_in=response.input_tokens or response.tokens_used or 0,
                    tokens_out=response.output_tokens or 0,
                )
            except Exception:  # pragma: no cover - defensive
                logger.debug("process_table.record_usage failed")

    def _transition(self, cont: Continuation, kind: str) -> None:
        if self._process_table is None:
            return
        try:
            from src.platform.kernel._process import Phase
        except Exception:  # pragma: no cover - kernel always present in repo
            return
        phase = {
            "running": Phase.RUNNING,
            "failed": Phase.FAILED,
        }.get(kind)
        if kind == "awaiting":
            human = (cont.suspend_reason or "") in SuspendReason.HUMAN_REASONS
            phase = Phase.AWAITING_HUMAN if human else _awaiting_external_phase()
        if phase is None:
            return
        try:
            self._process_table.transition(cont.pid, phase, force=True,
                                           reason=f"runtime:{cont.status}")
        except Exception:  # pragma: no cover - defensive
            logger.debug("process_table.transition failed")

    def _commit_ticket(self, ticket: str | None) -> None:
        if not ticket or self._kernel is None:
            return
        budgets = getattr(self._kernel, "budgets", None)
        if budgets is not None and hasattr(budgets, "commit"):
            try:
                budgets.commit(ticket)
            except Exception:  # pragma: no cover - defensive
                logger.debug("budget commit failed for ticket %s", ticket)

    def _release_ticket(self, ticket: str | None) -> None:
        if not ticket or self._kernel is None:
            return
        budgets = getattr(self._kernel, "budgets", None)
        if budgets is not None and hasattr(budgets, "release"):
            try:
                budgets.release(ticket)
            except Exception:  # pragma: no cover - defensive
                logger.debug("budget release failed for ticket %s", ticket)


def _awaiting_external_phase():
    """Return Phase.AWAITING_EXTERNAL, falling back to AWAITING_HUMAN until the
    new phase lands in ``_process.py`` (added in this branch)."""
    from src.platform.kernel._process import Phase

    return getattr(Phase, "AWAITING_EXTERNAL", Phase.AWAITING_HUMAN)


__all__ = ["RunOutcome", "RunStatus", "StepEngine", "MAX_TOOL_TURNS"]
