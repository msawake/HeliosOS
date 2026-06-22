"""Agent chat + SSE endpoints.

Ported 1:1 from the ``create_fastapi_app`` factory in
src/dashboard/fastapi_app.py. Paths, SSE frame contract, and JSON response
shapes are the contract and are preserved exactly.

SSE wiring
----------
The FastAPI chat-stream endpoints do NOT token-stream from the LLM. They run a
COMPLETED ``AgentResult`` (or resumed ``RunOutcome``) to a finished state, then
translate it into a fixed sequence of frames:

    session  ->  (tool_call / tool_result / text_delta / hitl_request)*  ->  done

(an ``error`` frame replaces the body on failure, always followed by ``done``).

Here that exact contract is served from a PLAIN Django ``async def`` view via
``StreamingHttpResponse`` so a long stream does not pin a sync worker. Agents
execute ONLY on the Celery worker tier (broker=Redis): the generator enqueues
``forgeos.run_agent`` via ``celery.send_task``, polls the task result, then
replays it as the frame sequence above. The web process never invokes inline.

The JSON endpoints (history, sessions list, sessions delete, wizard/chat) are
ordinary DRF ``APIView``s and match the FastAPI response shapes byte-for-byte.

Auth (follow-up)
----------------
The FastAPI chat-stream routes ran ``Depends(current_user)`` (which first runs
``check_auth``). Plain Django async views bypass DRF authentication/permission
classes, so these SSE views replicate only the *identity* behavior: read the
acting user from the ``X-Forgeos-User`` header (default ``"default"``), with no
role enforcement (the FastAPI chat routes enforced no role). The JSON APIViews
keep the DRF default auth from settings.
TODO(step8-auth): wire DRF-equivalent authentication for the async SSE views
(e.g. an ASGI auth middleware or a manual ForgeOSAuthentication call) so the
stream routes are gated the same way ``check_auth`` gated them in FastAPI.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from django.db import transaction
from django.http import StreamingHttpResponse
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web import di
from forgeos_web.authn.context import acting_user

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Session store
# --------------------------------------------------------------------------- #
# FastAPI kept this as a factory-local in-memory dict
# (``_chat_sessions: dict[str, dict]`` at fastapi_app.py:1497). Replicated here
# as a module-global so behavior matches (single-process, in-memory). The
# executor threads its own multi-turn history via ``session_id``; this dict only
# backs the sessions-list / history / delete views and the assistant-turn
# persistence the stream view does.
# TODO(step8-store): move to a shared/Redis-backed store so it survives multiple
# workers and process restarts (and so the stale-session eviction the FastAPI
# app ran as a background task can be reinstated).
_chat_sessions: dict[str, dict] = {}


# --------------------------------------------------------------------------- #
# SSE helpers
# --------------------------------------------------------------------------- #
def _sse(payload: dict) -> str:
    """Encode one SSE frame: ``data: {json}\\n\\n`` (default=str for non-JSON)."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _sse_response(generator) -> StreamingHttpResponse:
    """Wrap an async generator of SSE strings with the headers the chat UI /
    proxies expect: text/event-stream, no caching, no nginx buffering."""
    resp = StreamingHttpResponse(generator, content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


def _resume_agent_context(cont):
    """Rebuild the per-agent ``agent_context`` for a resumed continuation.

    Ported from fastapi_app.py:753. Without this, ``engine.resume`` runs with
    ``agent_context=None`` and an approved tool re-executes stripped of the
    agent's identity (e.g. a per-agent Drive SA never bound). Resolve the agent
    from the registry by the continuation's pid and rebuild the same context the
    initial invoke used.
    """
    ctx = di.try_get_context() or di.AppContext()
    platform_registry = ctx.platform_registry
    platform_executor = ctx.platform_executor
    try:
        agent_def = platform_registry.get(cont.pid) if platform_registry else None
        if agent_def is None and platform_executor:
            agent_def = platform_executor.registry.get(cont.pid)
        if agent_def is None:
            return None
        from stacks.base import build_agent_context

        return build_agent_context(
            agent_def, agent_def.agent_id,
            context={"user_id": getattr(cont, "user_id", "default")},
        )
    except Exception:
        logger.debug("resume: could not rebuild agent_context for %s", getattr(cont, "pid", "?"))
        return None


async def _resume_v2_continuation_await(request_id: str, accept: bool, responded_by):
    """AWAIT the resume of a runtime-v2 continuation parked on ``request_id`` and
    return the resulting ``RunOutcome`` (None if no matching continuation).

    Ported from fastapi_app.py:828. Used by the chat resume stream so it can show
    the continued result inline.
    """
    ctx = di.try_get_context() or di.AppContext()
    platform_executor = ctx.platform_executor
    if not platform_executor:
        return None
    from src.runtime import Resolution, ResolutionOutcome

    for adapter in getattr(platform_executor, "_adapters", {}).values():
        engine = getattr(adapter, "step_engine", None)
        store = getattr(engine, "_store", None)
        if store is None:
            continue
        try:
            cont = store.find_by_external_ref(request_id)
        except Exception:
            cont = None
        if cont is None:
            continue
        rec = next((r for r in cont.pending_calls if r.external_ref == request_id), None)
        if rec is None:
            continue
        token_id = None
        kernel = getattr(engine, "_kernel", None)
        if accept and kernel is not None and hasattr(kernel, "issue_capability"):
            tok = kernel.issue_capability(
                subject=cont.pid, target=f"tool:{rec.name}", verb="tool.call",
                ttl_seconds=3600,
                metadata={"external_ref": request_id, "continuation_id": cont.continuation_id},
            )
            token_id = tok.id
        resolution = Resolution(
            continuation_id=cont.continuation_id, tool_use_id=rec.tool_use_id,
            outcome=ResolutionOutcome.ACCEPT if accept else ResolutionOutcome.REJECT,
            capability_token=token_id, responded_by=responded_by,
        )
        return await engine.resume(
            resolution, tool_executor=getattr(adapter, "_tool_executor", None),
            agent_context=_resume_agent_context(cont),
        )
    return None


# --------------------------------------------------------------------------- #
# POST /api/platform/agents/{agent_id}/chat/stream  (SSE)
# --------------------------------------------------------------------------- #
@transaction.non_atomic_requests  # async views are incompatible with ATOMIC_REQUESTS
async def agent_chat_stream(request, agent_id: str):
    """Multi-turn streaming chat with an agent. Ported from fastapi_app.py:1501.

    Streams: session -> tool/text/hitl frames -> done (error+done on failure).
    """
    user = acting_user(request)
    try:
        body = json.loads(request.body or b"{}")
    except Exception:
        body = {}
    message = body.get("message")

    ctx = di.try_get_context() or di.AppContext()
    platform_executor = ctx.platform_executor
    platform_registry = ctx.platform_registry

    # HTTPException(500/404) in FastAPI -> emit the equivalent error frame in the
    # stream (the endpoint already returned 200 with an event stream by the time
    # FastAPI raised inside the handler body it raised BEFORE the StreamingResponse;
    # here we surface pre-flight failures as an error+done frame so the contract
    # stays a stream). The frame body mirrors the FastAPI detail text.
    if not platform_executor:
        async def _err_no_exec():
            yield _sse({"type": "error", "error": "Platform executor not available"})
            yield _sse({"type": "done", "tokens_used": 0, "text": ""})
        return _sse_response(_err_no_exec())

    agent_def = platform_registry.get(agent_id) if platform_registry else None
    if not agent_def:
        async def _err_not_found():
            yield _sse({"type": "error", "error": f"Agent {agent_id} not found"})
            yield _sse({"type": "done", "tokens_used": 0, "text": ""})
        return _sse_response(_err_not_found())

    # Session management (atomic via setdefault to avoid race conditions).
    sid = body.get("session_id") or str(uuid.uuid4())
    session = _chat_sessions.setdefault(sid, {
        "agent_id": agent_id,
        "messages": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    session["messages"].append({"role": "user", "content": message})

    async def generate():
        import asyncio as _asyncio

        yield _sse({"type": "session", "session_id": sid})
        # Agents run ONLY on the Celery worker tier (broker=Redis). Enqueue the
        # run, poll the task result, then replay it as the SSE frame contract
        # (session -> tool/text -> done). No inline execution in the web process.
        from forgeos_web.celery_app import celery
        try:
            task = celery.send_task(
                "forgeos.run_agent",
                kwargs={"agent_id": agent_id, "prompt": message,
                        "context": {"session_id": sid, "chat_id": sid, "user_id": user},
                        "session_id": sid, "tenant_id": (ctx.tenant_id or "default")},
                queue="agents",
            )
        except Exception:
            logger.exception("Could not enqueue agent run for %s", agent_id)
            yield _sse({"type": "error", "error": "Could not enqueue agent run (broker down?)"})
            yield _sse({"type": "done", "tokens_used": 0, "text": ""})
            return

        waited = 0.0
        while not task.ready() and waited < 300:
            await _asyncio.sleep(0.5)
            waited += 0.5
        if not task.ready():
            yield _sse({"type": "error", "error": "Timed out waiting for the worker (is the Celery worker running?)"})
            yield _sse({"type": "done", "tokens_used": 0, "text": ""})
            return

        if not task.successful():
            yield _sse({"type": "error", "error": f"Agent run failed: {task.result}"})
            yield _sse({"type": "done", "tokens_used": 0, "text": ""})
            return

        d = task.result or {}
        if d.get("error") and not d.get("output"):
            yield _sse({"type": "error", "error": d["error"]})
        for tc in (d.get("tool_calls") or []):
            tuid = tc.get("tool_use_id")
            yield _sse({"type": "tool_call", "name": tc.get("name"),
                        "input": tc.get("input") or {}, "tool_use_id": tuid})
            yield _sse({"type": "tool_result", "name": tc.get("name"),
                        "result": tc.get("result"), "tool_use_id": tuid,
                        "is_error": tc.get("is_error", False)})
        if d.get("output"):
            yield _sse({"type": "text_delta", "content": d["output"]})
            session["messages"].append({"role": "assistant", "content": d["output"]})
        # Gated tools: when the run SUSPENDED for human approval, surface each
        # pending call as a `hitl_request` frame so the chat renders an approval
        # card (the dashboard contract — see the module docstring's frame list —
        # and chat/page.tsx `case 'hitl_request'`). Without this the run looks
        # like it silently "didn't respond". external_ref is the approvals
        # request_id the card POSTs to /api/approvals/<id>/approve.
        for p in (d.get("pending") or []):
            ref = p.get("external_ref")
            if not ref:
                continue
            tool = p.get("name")
            yield _sse({
                "type": "hitl_request",
                "request_id": ref,
                "tool": tool,
                "args": p.get("arguments") or {},
                "title": f"Approve tool '{tool}'?",
                "description": "This action is gated by your governance policy "
                               "and needs human approval before it runs.",
                "risk": "high",
            })
        yield _sse({"type": "done", "tokens_used": d.get("tokens_used", 0),
                    "text": d.get("output", ""), "status": d.get("status")})

    return _sse_response(generate())


# --------------------------------------------------------------------------- #
# POST /api/platform/agents/{agent_id}/chat/resume  (SSE)
# --------------------------------------------------------------------------- #
@transaction.non_atomic_requests  # async views are incompatible with ATOMIC_REQUESTS
async def agent_chat_resume(request, agent_id: str):
    """Approve a parked (ask_human) chat run and stream the continued result.
    Ported from fastapi_app.py:1552. Body: {request_id, session_id}.

    NOTE: in FastAPI a missing request_id raised HTTPException(400) BEFORE the
    StreamingResponse was constructed (a true 400). Preserved here as a 400 JSON
    error rather than an in-stream frame.
    """
    user = acting_user(request)
    try:
        body = json.loads(request.body or b"{}")
    except Exception:
        body = {}
    request_id = (body or {}).get("request_id")
    sid = (body or {}).get("session_id")
    if not request_id:
        from django.http import JsonResponse
        return JsonResponse({"detail": "request_id required"}, status=400)

    async def generate():
        yield _sse({"type": "session", "session_id": sid})
        try:
            from src.dashboard.chat_events import run_outcome_to_chat_events

            outcome = await _resume_v2_continuation_await(
                request_id, accept=True, responded_by=user,
            )
            for ev in run_outcome_to_chat_events(outcome):
                yield _sse(ev)
            resumed_output = getattr(outcome, "output", None) if outcome is not None else None
            if resumed_output and sid:
                if sid in _chat_sessions:
                    _chat_sessions[sid]["messages"].append(
                        {"role": "assistant", "content": resumed_output})
                # Backfill the assistant turn the PAUSED invoke withheld so the
                # NEXT chat turn sees what the agent actually did.
                ctx = di.try_get_context() or di.AppContext()
                platform_executor = ctx.platform_executor
                if platform_executor is not None:
                    try:
                        platform_executor.record_resumed_turn(
                            sid, resumed_output,
                            tokens_used=getattr(outcome, "tokens_used", 0) or 0,
                        )
                    except Exception:
                        logger.debug("record_resumed_turn failed", exc_info=True)
        except Exception:
            logger.exception("Agent chat resume error for %s", agent_id)
            yield _sse({"type": "error", "error": "Internal server error"})
            yield _sse({"type": "done", "tokens_used": 0, "text": ""})

    return _sse_response(generate())


# --------------------------------------------------------------------------- #
# POST /api/admin/chat/stream  (SSE)
# --------------------------------------------------------------------------- #
@transaction.non_atomic_requests  # async views are incompatible with ATOMIC_REQUESTS
async def admin_chat_stream(request):
    """Real SSE streaming admin chat. Ported from fastapi_app.py:2020.

    When an LLM router is configured with a real provider, streams tokens as they
    arrive. Otherwise falls back to fast-path command responses or the legacy
    chunked emulation via admin_invoker.
    """
    import asyncio

    try:
        body = json.loads(request.body or b"{}")
    except Exception:
        body = {}
    # ChatRequest: {message, session_id="default"}.
    req_message = body.get("message", "")

    ctx = di.try_get_context() or di.AppContext()
    platform_registry = ctx.platform_registry
    admin_tools = ctx.admin_tools
    admin_invoker = ctx.admin_invoker
    admin_registry = ctx.admin_registry
    llm_router = ctx.llm_router
    company_system = ctx.company_system

    async def generate():
        yield _sse({"type": "thinking", "content": "Processing..."})
        msg = (req_message or "").strip()
        msg_lower = msg.lower().strip()

        # Path 0: known commands handled instantly (no LLM needed).
        fast_response = None

        if msg_lower in ("list agents", "show agents", "agents"):
            agents_list = []
            if platform_registry:
                agents_list = [a.to_dict() for a in platform_registry.list_all()]
            if admin_tools:
                agents_list.extend(admin_tools.list_agents())
            if agents_list:
                lines = [f"**{len(agents_list)} agents registered:**\n"]
                for a in agents_list:
                    name = a.get("name", a.get("agent_id", "?"))
                    status = a.get("status", "?")
                    stack = a.get("stack", "?")
                    lines.append(f"- **{name}** ({stack}) — {status}")
                fast_response = "\n".join(lines)
            else:
                fast_response = "No agents registered. Deploy one via the AI Wizard or manual form."

        elif msg_lower in ("system status", "status", "health"):
            parts = ["**System Status:**\n"]
            if platform_registry:
                s = platform_registry.summary()
                parts.append(f"- Agents: {s.get('total', 0)} total, {s.get('running', 0)} running")
            if company_system:
                try:
                    pending = len(company_system.hitl.get_pending())
                    parts.append(f"- Pending approvals: {pending}")
                except Exception:
                    pass
            if llm_router:
                parts.append(f"- LLM providers: {', '.join(llm_router.available_providers())}")
            fast_response = "\n".join(parts)

        elif msg_lower in ("show approvals", "pending approvals", "approvals"):
            if company_system:
                pending = company_system.hitl.get_pending()
                if pending:
                    lines = [f"**{len(pending)} pending approvals:**\n"]
                    for p in pending[:10]:
                        lines.append(f"- **{p.get('title', '?')}** ({p.get('category', '?')}) — risk: {p.get('risk_assessment', '?')}")
                    fast_response = "\n".join(lines)
                else:
                    fast_response = "No pending approvals."
            else:
                fast_response = "HITL system not available."

        elif msg_lower in ("help", "?"):
            fast_response = (
                "Available commands:\n"
                "- `list agents` — show all registered agents\n"
                "- `system status` — health check\n"
                "- `show approvals` — pending HITL items\n"
                "- Or ask any question — I'll answer using the LLM"
            )

        if fast_response:
            yield _sse({"type": "text_delta", "content": fast_response})
            yield _sse({"type": "done", "tokens_used": 0})
            return

        # Path 1: LLM router. NON-STREAMING by design — the migration target
        # requires LLM calls not to stream. We call the non-streaming
        # llm_router.chat() and replay the completed answer as a single text
        # frame, preserving the SSE envelope (session->text->done) the dashboard
        # consumes. (Token-by-token chat_stream is intentionally not used here;
        # it is retired with the legacy app at cutover.)
        if llm_router and "simulated" not in llm_router.available_providers():
            try:
                from stacks.base import LLMConfig

                providers_available = llm_router.available_providers()
                if "anthropic" in providers_available:
                    cfg = LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic")
                elif "openai" in providers_available:
                    cfg = LLMConfig(chat_model="gpt-4o", provider="openai")
                else:
                    cfg = LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic")

                messages = [
                    {"role": "system",
                     "content": "You are the Helios OS admin assistant. Respond concisely."},
                    {"role": "user", "content": msg},
                ]
                result = await llm_router.chat(cfg, messages)
                # chat() returns a provider-normalized result; extract text robustly.
                text = (
                    getattr(result, "text", None)
                    or getattr(result, "content", None)
                    or (result.get("content") if isinstance(result, dict) else None)
                    or (str(result) if result is not None else "")
                )
                tokens = getattr(result, "tokens_used", 0)
                if isinstance(result, dict):
                    tokens = result.get("tokens_used", tokens)
                if text:
                    yield _sse({"type": "text_delta", "content": text})
                yield _sse({"type": "done", "tokens_used": tokens})
                return
            except Exception:
                logger.exception("LLM chat path failed, falling back")
                yield _sse({"type": "error", "error": "Internal server error"})
                return

        # Path 2: legacy fallback via admin_invoker (chunked emulation).
        if admin_invoker and admin_registry:
            try:
                result = await admin_invoker.invoke("admin-orchestrator", msg)
                text = result.result or "No response."
                words = text.split()
                for i in range(0, len(words), 3):
                    chunk = " ".join(words[i:i + 3])
                    yield _sse({"type": "text_delta", "content": chunk + " "})
                    await asyncio.sleep(0.05)
            except Exception:
                logger.exception("Admin invoke failed")
                yield _sse({"type": "error", "error": "Internal server error"})
        else:
            yield _sse({"type": "text_delta", "content": "Admin agent not available."})
        yield _sse({"type": "done", "tokens_used": 0})

    return _sse_response(generate())


# --------------------------------------------------------------------------- #
# GET /api/platform/agents/{agent_id}/chat/sessions
# --------------------------------------------------------------------------- #
class ChatSessionsView(APIView):
    """List all chat sessions for an agent. Ported from fastapi_app.py:1603."""

    def get(self, request, agent_id: str):
        sessions = []
        for sid, data in _chat_sessions.items():
            if data.get("agent_id") == agent_id:
                msgs = data.get("messages", [])
                preview = ""
                if msgs:
                    first_user = next((m["content"] for m in msgs if m["role"] == "user"), "")
                    preview = first_user[:100]
                sessions.append({
                    "session_id": sid,
                    "created_at": data.get("created_at", ""),
                    "message_count": len(msgs),
                    "preview": preview,
                })
        sessions.sort(key=lambda s: s["created_at"], reverse=True)
        return Response(sessions)


# --------------------------------------------------------------------------- #
# GET /api/platform/agents/{agent_id}/chat/history
# --------------------------------------------------------------------------- #
class ChatHistoryView(APIView):
    """Get the full message history for a chat session.
    Ported from fastapi_app.py:1623. ``session_id`` is a required query param."""

    def get(self, request, agent_id: str):
        session_id = request.query_params.get("session_id")
        if session_id is None:
            # FastAPI's Query(...) makes session_id required -> 422 on absence.
            return Response(
                {"detail": [{"loc": ["query", "session_id"], "msg": "field required",
                             "type": "value_error.missing"}]},
                status=422,
            )
        session = _chat_sessions.get(session_id)
        if not session or session.get("agent_id") != agent_id:
            return Response({"detail": "Session not found"}, status=404)
        return Response({
            "session_id": session_id,
            "agent_id": agent_id,
            "messages": session.get("messages", []),
            "created_at": session.get("created_at", ""),
        })


# --------------------------------------------------------------------------- #
# DELETE /api/platform/agents/{agent_id}/chat/sessions/{session_id}
# --------------------------------------------------------------------------- #
class ChatSessionDetailView(APIView):
    """Delete a chat session. Ported from fastapi_app.py:1636."""

    def delete(self, request, agent_id: str, session_id: str):
        session = _chat_sessions.get(session_id)
        if not session or session.get("agent_id") != agent_id:
            return Response({"detail": "Session not found"}, status=404)
        del _chat_sessions[session_id]
        return Response({"ok": True})


# --------------------------------------------------------------------------- #
# POST /api/platform/wizard/chat
# --------------------------------------------------------------------------- #
class WizardChatView(APIView):
    """AI-assisted agent design: conversational turn with optional deploy
    proposal. Ported from fastapi_app.py:1786 (a normal JSON invoke)."""

    def post(self, request):
        from asgiref.sync import async_to_sync

        body = request.data if isinstance(request.data, dict) else {}
        messages = body.get("messages") or []
        context = body.get("context") or {}
        cleaned = [
            {"role": m["role"], "content": m["content"].strip()}
            for m in messages
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
            and (m.get("content") or "").strip()
        ]
        if not cleaned or cleaned[-1]["role"] != "user":
            return Response({"detail": "last message must be a non-empty user message"}, status=400)

        ctx = di.try_get_context() or di.AppContext()
        platform_executor = ctx.platform_executor
        platform_registry = ctx.platform_registry
        llm_router = ctx.llm_router
        try:
            from src.platform.wizard_agent import run_wizard_turn as wizard_v2

            # Get tool_executor from the Helios OS adapter if available.
            _te = None
            if platform_executor:
                _fos = platform_executor.get_adapter("forgeos")
                if _fos:
                    _te = getattr(_fos, "_tool_executor", None)
            # TODO(step8-enqueue): port faithfully with async_to_sync so behavior
            # matches; the enqueue refactor of invoke-style calls is a later step.
            result = async_to_sync(wizard_v2)(
                llm_router, cleaned, context,
                platform_registry=platform_registry,
                tool_executor=_te,
            )
            return Response(result)
        except Exception:
            logger.exception("Wizard error")
            return Response({"detail": "Internal server error"}, status=500)


# SSE endpoints are POST-only (matches the FastAPI contract). Tag the async
# function views so the route-parity extractor records the correct method.
for _sse_view in (agent_chat_stream, agent_chat_resume, admin_chat_stream):
    _sse_view.forgeos_methods = ("POST",)
