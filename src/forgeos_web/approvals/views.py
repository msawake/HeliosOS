"""Approvals + HITL + A2H (agent-to-human) endpoints.

Ported 1:1 from src/dashboard/fastapi_app.py. Paths, response shapes, and
status codes are preserved so the Next.js dashboard / `forgeos` CLI contract is
unchanged.

Platform singletons come from the DI context (``src.forgeos_web.di``) instead of
the FastAPI factory closures. The A2H gateway and chat store are not direct
AppContext fields — they are resolved at request time by walking
``ctx.kernel`` → admission → tool_executor → ``_a2h_gateway`` (and ``.chat`` for
the chat store), exactly as the FastAPI factory did via ``_resolve_a2h_gateway``.

Async platform methods (``gw.ask``, ``platform_executor.invoke``,
``engine.resume``, ``runtime_service.resume.*``) are dispatched from these sync
DRF views via ``asgiref.async_to_sync`` so behavior matches the FastAPI handlers.
The /invoke enqueue refactor is a later step; these are ported faithfully for now.
"""

from __future__ import annotations

import json
import logging

from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from src.forgeos_web import di
from src.forgeos_web.authn.context import acting_user
from src.forgeos_web.authn.permissions import require_role

logger = logging.getLogger(__name__)


def _audit(action: str, **fields) -> None:
    # Lightweight audit hook; the platform audit sink (src.platform.audit.AuditLog,
    # fastapi_app.py:327) is wired in a later step. TODO: wire the real audit sink.
    logger.info("audit %s %s", action, fields)


# --------------------------------------------------------------------------- #
# A2H gateway resolution (ported from fastapi_app.py:330-342, 4085-4089)
# --------------------------------------------------------------------------- #
def _resolve_a2h_gateway(ctx):
    """Walk kernel → admission → tool_executor to reach the live A2HGateway.
    Returns None if any link is missing. (fastapi_app.py:330-342)"""
    kernel = getattr(ctx, "kernel", None)
    try:
        adm = getattr(kernel, "admission", None) if kernel is not None else None
        te = (
            (getattr(adm, "_tool_executor", None) if adm else None)
            or (getattr(kernel, "_tool_executor", None) if kernel else None)
            or (getattr(kernel, "tool_executor", None) if kernel else None)
        )
        return getattr(te, "_a2h_gateway", None) if te else None
    except Exception:
        return None


def _resolve_chat_gw(ctx):
    """A2H chat store lives on the gateway as ``gw.chat`` (fastapi_app.py:4085-4089)."""
    gw = _resolve_a2h_gateway(ctx)
    if gw is None or not hasattr(gw, "chat"):
        return None
    return gw.chat


# --------------------------------------------------------------------------- #
# Runtime-v2 pending approvals (factory-local helper, fastapi_app.py:651-699)
# --------------------------------------------------------------------------- #
def _list_v2_pending_approvals() -> list:
    """Pending human approvals from runtime-v2 suspended continuations.

    A run parked on ask_human is held in the adapter's StepEngine store
    (indexed by external_ref), not in the legacy HITL store. We scan the
    suspended continuations and emit an approval item per pending, human-gated
    tool call, carrying run_id/tool so clients can correlate the approval to the
    run it blocks. (fastapi_app.py:651-699)"""
    out: list = []
    platform_executor = getattr(di.try_get_context(), "platform_executor", None)
    if not platform_executor:
        return out
    seen_refs: set = set()  # adapters may share one StepEngine store — dedupe
    for adapter in getattr(platform_executor, "_adapters", {}).values():
        engine = getattr(adapter, "step_engine", None)
        store = getattr(engine, "_store", None)
        if store is None or not hasattr(store, "list_suspended"):
            continue
        try:
            suspended = store.list_suspended()
        except Exception:
            continue
        for cont in suspended:
            if (cont.suspend_reason or "") not in ("human_approval", "human_input"):
                continue
            for rec in cont.pending_calls:
                if rec.status != "pending" or not rec.external_ref:
                    continue
                if rec.external_ref in seen_refs:
                    continue
                seen_refs.add(rec.external_ref)
                q = f"Approve tool '{rec.name}' for agent {cont.pid}?"
                out.append({
                    "source": "runtime",
                    "id": rec.external_ref,
                    "request_id": rec.external_ref,
                    "run_id": cont.continuation_id,
                    "continuation_id": cont.continuation_id,
                    "tool": rec.name,
                    "agent_id": cont.pid,
                    "agent": cont.pid,
                    "status": "pending",
                    "risk": "high",
                    "created_at": cont.updated_at,
                    "title": q,
                    "question": q,
                    "content": {"question": q},
                })
    return out


def _resume_agent_context(cont):
    """Rebuild the per-agent ``agent_context`` for a resumed continuation.
    (fastapi_app.py:753-776)"""
    ctx = di.try_get_context()
    platform_registry = getattr(ctx, "platform_registry", None)
    platform_executor = getattr(ctx, "platform_executor", None)
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


def _resume_v2_continuation(request_id: str, accept: bool, responded_by: str | None) -> bool:
    """Resume a runtime-v2 continuation parked on this approval request.
    (fastapi_app.py:778-826)

    The FastAPI version schedules ``engine.resume`` via ``asyncio.create_task`` so
    the HTTP request returns immediately. In a sync DRF view there is no running
    loop to attach a background task to, so we dispatch the resume synchronously
    via ``async_to_sync`` before returning. Behavior (which continuation resumes,
    capability token minting) is identical; the resume is awaited rather than
    fire-and-forget. TODO: move resume onto the worker tier (enqueue) like
    runtime_service does, to restore the return-immediately behavior."""
    platform_executor = getattr(di.try_get_context(), "platform_executor", None)
    if not platform_executor:
        return False
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
        async_to_sync(engine.resume)(
            resolution,
            tool_executor=getattr(adapter, "_tool_executor", None),
            agent_context=_resume_agent_context(cont),
        )
        return True
    return False


def _resume_after_human_response(ctx, request_id: str) -> None:
    """When a human approves/rejects, wake the originating agent if it has no
    more pending requests. (fastapi_app.py:3886-3965)

    The FastAPI version schedules the resume invoke via ``asyncio.create_task``.
    In a sync DRF view we dispatch it synchronously via ``async_to_sync``.
    TODO: move this resume invoke onto the worker tier (enqueue)."""
    gw = _resolve_a2h_gateway(ctx)
    platform_executor = getattr(ctx, "platform_executor", None)
    if gw is None or platform_executor is None:
        return
    req = gw.get_request_obj(request_id) if hasattr(gw, "get_request_obj") else None
    from_agent = getattr(req, "from_agent", None) if req else None
    if not from_agent:
        return
    try:
        still_pending = gw.list_pending_from(from_agent)
    except Exception:
        still_pending = []
    try:
        from src.platform.kernel._process import Phase
        target = Phase.RUNNING if not still_pending else Phase.AWAITING_HUMAN
        platform_executor.process_table.transition(
            from_agent, target, force=True,
            reason="human responded",
        )
    except Exception:
        logger.debug("phase transition after human response failed", exc_info=True)
    if still_pending:
        return  # keep AWAITING_HUMAN; nothing to resume yet
    try:
        resume_prompt = (
            "Resume: every pending human approval has been resolved. "
            "Continue any deferred work."
        )
        resume_context = {"_trigger": "a2h_resume"}
        try:
            resolved = []
            if hasattr(gw, "list_resolved_from"):
                resolved = gw.list_resolved_from(from_agent, limit=50)
            if resolved:
                items = []
                for r in resolved:
                    rctx = getattr(r, "context", {}) or {}
                    resp = getattr(r, "response", None)
                    items.append({
                        "request_id": r.id,
                        "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                        "value": getattr(resp, "value", None) if resp else None,
                        "approved": getattr(resp, "approved", None) if resp else None,
                        "question": getattr(r, "question", "") or "",
                        "issue_key": rctx.get("issue_key"),
                        "context": rctx,
                        "responded_by": getattr(resp, "responded_by", None) if resp else None,
                    })
                resume_context["resolved_a2h_requests"] = items
                blob = json.dumps(items, indent=2, default=str)
                resume_prompt += (
                    "\n\nThe outcomes of your recent human__ask calls are below. "
                    "For each entry, you do NOT need to re-ask — the human has already "
                    "responded. Act on the outcome directly (approved → comment; rejected → skip):\n"
                    f"```json\n{blob}\n```"
                )
        except Exception:
            logger.debug("could not enrich resume prompt with resolved A2H data", exc_info=True)

        async_to_sync(platform_executor.invoke)(
            from_agent,
            resume_prompt,
            resume_context,
        )
        logger.info("A2H resume invoke scheduled for %s after %s", from_agent, request_id)
    except Exception:
        logger.exception("resume invoke failed for %s", from_agent)


def _a2h_respond_error(result: dict, request_id: str, gw):
    """Map gateway respond() failure to a meaningful (detail, status).
    (fastapi_app.py:3967-3977)"""
    err = (result.get("error") or "").lower()
    req = gw.get_request_obj(request_id) if hasattr(gw, "get_request_obj") else None
    if req is None:
        return Response({"detail": f"A2H request '{request_id}' not found"}, status=404)
    if "not pending" in err or "expired" in err or "cancelled" in err:
        return Response({"detail": result.get("error") or "Request is no longer pending"}, status=409)
    return Response({"detail": result.get("error") or "Failed"}, status=400)


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
class ApprovalActionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    approved_by = serializers.CharField(required=False, allow_blank=True, default="")
    rejected_by = serializers.CharField(required=False, allow_blank=True, default="")


class A2HAskSerializer(serializers.Serializer):
    to_namespace = serializers.CharField(required=False, allow_blank=True, default="default")
    to_name = serializers.CharField(required=False, allow_blank=True, default="")
    question = serializers.CharField(required=False, allow_blank=True, default="")
    response_type = serializers.CharField(required=False, allow_blank=True, default="text")
    options = serializers.ListField(required=False, allow_null=True, default=None)
    context = serializers.DictField(required=False, allow_null=True, default=None)
    priority = serializers.CharField(required=False, allow_blank=True, default="medium")
    deadline = serializers.CharField(required=False, allow_null=True, default=None)
    from_agent = serializers.CharField(required=False, allow_blank=True, default="")


class A2HRespondSerializer(serializers.Serializer):
    response = serializers.DictField(required=False, default=dict)
    channel = serializers.CharField(required=False, allow_blank=True, default="dashboard")
    responded_by = serializers.CharField(required=False, allow_blank=True, default="")


class A2HNotifySerializer(serializers.Serializer):
    to_namespace = serializers.CharField(required=False, allow_blank=True, default="default")
    to_name = serializers.CharField(required=False, allow_blank=True, default="")
    message = serializers.CharField(required=False, allow_blank=True, default="")
    priority = serializers.CharField(required=False, allow_blank=True, default="low")
    context = serializers.DictField(required=False, allow_null=True, default=None)
    from_agent = serializers.CharField(required=False, allow_blank=True, default="")


# --------------------------------------------------------------------------- #
# Approvals (fastapi_app.py:701-917)
# --------------------------------------------------------------------------- #
class ApprovalsView(APIView):
    """GET /api/approvals — public list (no role gate; public GET prefix in
    IsAuthenticatedOrPublicPath)."""

    def get(self, request):
        ctx = di.try_get_context()
        category = request.query_params.get("category")
        company_system = getattr(ctx, "company_system", None)
        pending: list = []
        if company_system:
            try:
                pending = list(
                    company_system.hitl.get_pending(category) if category
                    else company_system.hitl.get_pending()
                )
            except Exception:
                pending = []
        try:
            gw = _resolve_a2h_gateway(ctx)
            if gw and hasattr(gw, "list_pending"):
                for it in gw.list_pending() or []:
                    content = it.get("content") or {}
                    frm = it.get("from") or {}
                    pending.append({
                        "source": "a2h",
                        "id": it.get("id"),
                        "agent": frm.get("name") or it.get("from_agent") or it.get("agent_id"),
                        "risk": it.get("priority", "medium"),
                        "timestamp": it.get("created_at"),
                        "title": content.get("question") or it.get("question") or it.get("message"),
                        "response_type": content.get("response_type") or it.get("response_type"),
                        "description": (content.get("context") or it.get("context") or {}),
                    })
        except Exception:
            pass
        try:
            pending.extend(_list_v2_pending_approvals())
        except Exception:
            pass
        return Response(pending)


class ApprovalDetailView(APIView):
    """GET /api/approvals/{request_id}."""

    def get(self, request, request_id):
        company_system = getattr(di.try_get_context(), "company_system", None)
        if not company_system:
            return Response({"detail": "System not initialized"}, status=404)
        item = company_system.hitl.check_status(request_id)
        if not item:
            return Response({"detail": "Not found"}, status=404)
        return Response(item)


class ApprovalApproveView(APIView):
    """POST /api/approvals/{request_id}/approve (admin/operator)."""

    permission_classes = [require_role("admin", "operator")]

    def post(self, request, request_id):
        ctx = di.try_get_context()
        ser = ApprovalActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        body = ser.validated_data
        company_system = getattr(ctx, "company_system", None)
        runtime_service = getattr(ctx, "runtime_service", None)
        handled = False
        if company_system:
            try:
                handled = bool(company_system.hitl.approve(
                    request_id, approved_by=body["approved_by"] or "api", reason=body["reason"]
                ))
            except Exception:
                logger.debug("legacy hitl.approve did not handle %s", request_id)
        if runtime_service is not None:
            resumed = bool(async_to_sync(runtime_service.resume.approve)(
                request_id, responded_by=body["approved_by"] or "api"))
        else:
            resumed = _resume_v2_continuation(request_id, accept=True,
                                              responded_by=body["approved_by"] or "api")
        if not handled and not resumed:
            return Response({"detail": f"No pending approval '{request_id}'"}, status=404)
        _audit("approval.approve", actor=body["approved_by"] or "api",
               resource_type="approval", resource_id=request_id,
               details={"reason": body["reason"], "resumed_run": resumed})
        return Response({"success": True, "resumed": resumed})


class ApprovalRejectView(APIView):
    """POST /api/approvals/{request_id}/reject (admin/operator)."""

    permission_classes = [require_role("admin", "operator")]

    def post(self, request, request_id):
        ctx = di.try_get_context()
        ser = ApprovalActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        body = ser.validated_data
        company_system = getattr(ctx, "company_system", None)
        runtime_service = getattr(ctx, "runtime_service", None)
        handled = False
        if company_system:
            try:
                handled = bool(company_system.hitl.reject(
                    request_id, rejected_by=body["rejected_by"] or "api", reason=body["reason"]
                ))
            except Exception:
                logger.debug("legacy hitl.reject did not handle %s", request_id)
        if runtime_service is not None:
            resumed = bool(async_to_sync(runtime_service.resume.reject)(
                request_id, responded_by=body["rejected_by"] or "api"))
        else:
            resumed = _resume_v2_continuation(request_id, accept=False,
                                              responded_by=body["rejected_by"] or "api")
        if not handled and not resumed:
            return Response({"detail": f"No pending approval '{request_id}'"}, status=404)
        _audit("approval.reject", actor=body["rejected_by"] or "api",
               resource_type="approval", resource_id=request_id,
               details={"reason": body["reason"], "resumed_run": resumed})
        return Response({"success": True, "resumed": resumed})


# --------------------------------------------------------------------------- #
# HITL / debug (fastapi_app.py:3429-3505)
# --------------------------------------------------------------------------- #
class DebugA2HView(APIView):
    """GET /api/_debug/a2h."""

    def get(self, request):
        ctx = di.try_get_context()
        kernel = getattr(ctx, "kernel", None)
        info = {"gw": None, "humans": [], "requests": []}
        try:
            gw = None
            te = None
            if kernel is not None:
                adm = getattr(kernel, "admission", None)
                te = (getattr(adm, "_tool_executor", None) if adm else None)
                if te is not None:
                    gw = getattr(te, "_a2h_gateway", None)
            info["gw"] = repr(gw)
            info["te_id"] = id(te) if te else None
            info["gw_id"] = id(gw) if gw else None
            if gw is not None:
                humans = getattr(gw, "_humans", {})
                info["humans"] = [{"pid": h.pid, "name": h.name, "ns": h.namespace, "state": h.current_state} for h in humans.values()]
                store = getattr(gw, "_store", None)
                if store is not None:
                    reqs = getattr(store, "_requests", {})
                    info["requests"] = [{"id": r.id, "status": r.status.value if hasattr(r.status, "value") else str(r.status), "ns": r.namespace, "to_human": r.to_human, "to_name": getattr(r, "to_human_name", None), "from": r.from_agent} for r in reqs.values()]
        except Exception as e:
            info["error"] = str(e)
        return Response(info)


class HitlPendingView(APIView):
    """GET /api/hitl/pending — unified pending HITL inbox."""

    def get(self, request):
        ctx = di.try_get_context()
        company_system = getattr(ctx, "company_system", None)
        kernel = getattr(ctx, "kernel", None)
        items: list[dict] = []
        try:
            if company_system and getattr(company_system, "hitl", None):
                pending = company_system.hitl.get_pending()
                for a in pending or []:
                    items.append({
                        "source": "approval",
                        "id": a.get("id"),
                        "agent_id": a.get("agent"),
                        "priority": a.get("risk", "medium"),
                        "created_at": a.get("timestamp"),
                        "question": a.get("title") or a.get("description"),
                        "context": {"description": a.get("description"), "category": a.get("category"), "deadline": a.get("deadline")},
                    })
        except Exception:
            pass
        try:
            gw = None
            if kernel is not None:
                adm = getattr(kernel, "admission", None)
                te = (getattr(adm, "_tool_executor", None) if adm else None) \
                    or getattr(kernel, "_tool_executor", None) \
                    or getattr(kernel, "tool_executor", None)
                if te is not None:
                    gw = getattr(te, "_a2h_gateway", None)
            if gw and hasattr(gw, "list_pending"):
                pend = gw.list_pending()
                logger.debug("hitl/pending: a2h list_pending returned %d items", len(pend or []))
                for it in pend or []:
                    content = it.get("content") or {}
                    frm = it.get("from") or {}
                    items.append({
                        "source": "a2h",
                        "id": it.get("id"),
                        "agent_id": frm.get("name") or it.get("from_agent") or it.get("agent_id"),
                        "priority": it.get("priority", "medium"),
                        "created_at": it.get("created_at"),
                        "question": content.get("question") or it.get("question") or it.get("message"),
                        "context": content.get("context") or it.get("context") or {},
                    })
            else:
                logger.debug("hitl/pending: a2h gateway not reachable (gw=%s)", gw)
        except Exception as e:
            logger.warning("hitl/pending: a2h section failed: %s", e)
        items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return Response({"items": items})


# --------------------------------------------------------------------------- #
# A2H protocol (fastapi_app.py:3855-4079)
# --------------------------------------------------------------------------- #
class A2HRequestsView(APIView):
    """POST /api/a2h/requests — create an A2H request (status 201)."""

    def post(self, request):
        ctx = di.try_get_context()
        ser = A2HAskSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = ser.validated_data
        gw = _resolve_a2h_gateway(ctx)
        if gw is None:
            return Response({"detail": "A2H gateway not available"}, status=503)
        result = async_to_sync(gw.ask)(
            from_agent=req["from_agent"] or "api",
            from_agent_name=req["from_agent"] or "api",
            to_namespace=req["to_namespace"],
            to_name=req["to_name"],
            question=req["question"],
            response_type=req["response_type"],
            options=req["options"],
            context=req["context"],
            priority=req["priority"],
            deadline=req["deadline"],
        )
        data = result.to_dict() if hasattr(result, "to_dict") else result
        return Response(data, status=201)


class A2HRequestDetailView(APIView):
    """GET /api/a2h/requests/{request_id}."""

    def get(self, request, request_id):
        gw = _resolve_a2h_gateway(di.try_get_context())
        if gw is None:
            return Response({"detail": "A2H gateway not available"}, status=503)
        result = gw.get_request(request_id)
        if not result:
            return Response({"detail": "Request not found"}, status=404)
        return Response(result)


class A2HApproveView(APIView):
    """POST /api/a2h/requests/{request_id}/approve."""

    def post(self, request, request_id):
        ctx = di.try_get_context()
        responded_by = request.query_params.get("responded_by", "operator")
        gw = _resolve_a2h_gateway(ctx)
        if gw is None:
            return Response({"detail": "A2H gateway not available"}, status=503)
        result = gw.respond(
            request_id,
            {"approved": True, "value": "approved"},
            responded_by=responded_by, via="dashboard",
        )
        if not result.get("success"):
            return _a2h_respond_error(result, request_id, gw)
        _resume_after_human_response(ctx, request_id)
        return Response(result)


class A2HRejectView(APIView):
    """POST /api/a2h/requests/{request_id}/reject."""

    def post(self, request, request_id):
        ctx = di.try_get_context()
        responded_by = request.query_params.get("responded_by", "operator")
        reason = request.query_params.get("reason", "")
        gw = _resolve_a2h_gateway(ctx)
        if gw is None:
            return Response({"detail": "A2H gateway not available"}, status=503)
        result = gw.respond(
            request_id,
            {"approved": False, "value": "rejected", "text": reason},
            responded_by=responded_by, via="dashboard",
        )
        if not result.get("success"):
            return _a2h_respond_error(result, request_id, gw)
        _resume_after_human_response(ctx, request_id)
        return Response(result)


class A2HRespondView(APIView):
    """POST /api/a2h/requests/{request_id}/respond."""

    def post(self, request, request_id):
        ctx = di.try_get_context()
        ser = A2HRespondSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = ser.validated_data
        gw = _resolve_a2h_gateway(ctx)
        if gw is None:
            return Response({"detail": "A2H gateway not available"}, status=503)
        result = gw.respond(
            request_id, req["response"],
            responded_by=req["responded_by"], via=req["channel"],
        )
        if not result.get("success"):
            return Response({"detail": result.get("error", "Failed")}, status=400)
        _resume_after_human_response(ctx, request_id)
        return Response(result)


class A2HPendingView(APIView):
    """GET /api/a2h/pending."""

    def get(self, request):
        gw = _resolve_a2h_gateway(di.try_get_context())
        if gw is None:
            return Response({"requests": []})
        to = request.query_params.get("to")
        return Response({"requests": gw.list_pending(to)})


class A2HNotificationsView(APIView):
    """POST /api/a2h/notifications (status 201)."""

    def post(self, request):
        ser = A2HNotifySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = ser.validated_data
        gw = _resolve_a2h_gateway(di.try_get_context())
        if gw is None:
            return Response({"detail": "A2H gateway not available"}, status=503)
        notif = async_to_sync(gw.notify)(
            from_agent=req["from_agent"] or "api",
            from_agent_name=req["from_agent"] or "api",
            to_namespace=req["to_namespace"],
            to_name=req["to_name"],
            message=req["message"],
            priority=req["priority"],
            context=req["context"],
        )
        data = notif.to_dict() if hasattr(notif, "to_dict") else {"delivered": True}
        return Response(data, status=201)


class A2HHumansView(APIView):
    """GET /api/a2h/humans ; POST /api/a2h/humans (status 201)."""

    def get(self, request):
        gw = _resolve_a2h_gateway(di.try_get_context())
        if gw is None:
            return Response({"humans": []})
        namespace = request.query_params.get("namespace")
        humans = gw.list_humans(namespace)
        return Response({"humans": [h.to_discovery_dict() for h in humans]})

    def post(self, request):
        gw = _resolve_a2h_gateway(di.try_get_context())
        if gw is None:
            return Response({"detail": "A2H gateway not available"}, status=503)
        body = request.data
        from src.platform.a2h import HumanAgent
        human = HumanAgent(
            pid=f"human:{body['name']}",
            name=body["name"],
            namespace=body.get("namespace", "default"),
            role=body.get("role", ""),
            channels=body.get("channels", ["dashboard"]),
        )
        pid = gw.register_human(human)
        return Response({"pid": pid, "name": human.name, "namespace": human.namespace}, status=201)


# --------------------------------------------------------------------------- #
# A2H chat (fastapi_app.py:4091-4240)
# --------------------------------------------------------------------------- #
class A2HChatsView(APIView):
    """GET /api/a2h/v1/chats ; POST /api/a2h/v1/chats (open, status 201)."""

    def get(self, request):
        gw = _resolve_chat_gw(di.try_get_context())
        if gw is None:
            return Response({"chats": []})
        agent_pid = request.query_params.get("agent_pid")
        human_pid = request.query_params.get("human_pid")
        status = request.query_params.get("status")
        return Response({"chats": gw.list(agent_pid=agent_pid, human_pid=human_pid, status=status)})

    def post(self, request):
        ctx = di.try_get_context()
        gw = _resolve_chat_gw(ctx)
        if gw is None:
            return Response({"detail": "A2H chat not available"}, status=503)
        platform_executor = getattr(ctx, "platform_executor", None)
        user = acting_user(request)
        body = request.data
        agent_ns = body.get("agent_namespace", "default")
        agent_name = body.get("agent_name")
        agent_pid = body.get("agent_pid") or body.get("agent_id") or ""
        human_name = body.get("human_name", "operator")
        human_ns = body.get("human_namespace", agent_ns)
        topic = body.get("topic", "")
        context = body.get("context") or {}
        context.setdefault("user_id", body.get("user_id") or user)

        if platform_executor and hasattr(platform_executor, "registry"):
            resolved = None
            if agent_pid:
                resolved = platform_executor.registry.get(agent_pid)
            if resolved is None and agent_name:
                for a in platform_executor.registry.list_all():
                    if getattr(a, "name", "") == agent_name and getattr(a, "namespace", "default") == agent_ns:
                        resolved = a
                        break
            if resolved is not None:
                agent_name = agent_name or getattr(resolved, "name", "")
                agent_ns = getattr(resolved, "namespace", agent_ns)
                agent_pid = getattr(resolved, "agent_id", "") or agent_pid
        if not agent_name:
            agent_name = agent_pid or "agent"

        session = gw.open_for_human(
            agent_pid=agent_pid or f"{agent_ns}/{agent_name}",
            agent_name=agent_name,
            namespace=agent_ns,
            human_pid=body.get("human_pid", f"human:{human_name}"),
            human_name=human_name,
            topic=topic, context=context,
        )
        return Response(session.to_dict(include_messages=False), status=201)


class A2HChatDetailView(APIView):
    """GET /api/a2h/v1/chats/{chat_id}."""

    def get(self, request, chat_id):
        gw = _resolve_chat_gw(di.try_get_context())
        if gw is None:
            return Response({"detail": "A2H chat not available"}, status=503)
        include_messages = request.query_params.get("include_messages", "true").lower() != "false"
        out = gw.get_session(chat_id, include_messages=include_messages)
        if out is None:
            return Response({"detail": "chat not found"}, status=404)
        return Response(out)


class A2HChatCloseView(APIView):
    """POST /api/a2h/v1/chats/{chat_id}/close."""

    def post(self, request, chat_id):
        gw = _resolve_chat_gw(di.try_get_context())
        if gw is None:
            return Response({"detail": "A2H chat not available"}, status=503)
        body = request.data if isinstance(request.data, dict) else {}
        return Response(gw.close(chat_id, reason=(body or {}).get("reason", "")))


class A2HChatMessagesView(APIView):
    """GET /api/a2h/v1/chats/{chat_id}/messages ;
    POST /api/a2h/v1/chats/{chat_id}/messages (status 201)."""

    def get(self, request, chat_id):
        gw = _resolve_chat_gw(di.try_get_context())
        if gw is None:
            return Response({"detail": "A2H chat not available"}, status=503)
        since = request.query_params.get("since")
        wait_seconds = float(request.query_params.get("wait_seconds", 0) or 0)
        if wait_seconds and wait_seconds > 0:
            return Response(async_to_sync(gw.wait)(chat_id=chat_id, since=since, timeout=float(wait_seconds)))
        return Response(gw.fetch(chat_id=chat_id, since=since))

    def post(self, request, chat_id):
        ctx = di.try_get_context()
        gw = _resolve_chat_gw(ctx)
        if gw is None:
            return Response({"detail": "A2H chat not available"}, status=503)
        platform_executor = getattr(ctx, "platform_executor", None)
        body = request.data
        role = body.get("role", "human")
        content = body.get("content", "")
        result = gw.post(chat_id=chat_id, role=role, sender=body.get("sender", ""), content=content)
        if not result.get("ok"):
            return Response({"detail": result.get("error", "post failed")}, status=400)

        # When a human speaks, invoke the target agent and post its reply back so
        # the chat is conversational. (fastapi_app.py:4156-4194) The FastAPI
        # version schedules this via asyncio.create_task so the POST returns
        # before the agent runs; here it is awaited synchronously via
        # async_to_sync. TODO: move the agent reply onto the worker tier so this
        # POST returns immediately.
        if role == "human" and content and platform_executor and not body.get("client_drives"):
            agent_pid = None
            agent_name = "agent"
            chat_user = None
            try:
                sess = gw.get_session(chat_id, include_messages=False) if hasattr(gw, "get_session") else None
                if isinstance(sess, dict):
                    agent_pid = sess.get("agent_pid")
                    agent_name = sess.get("agent_name") or "agent"
                    chat_user = (sess.get("context") or {}).get("user_id")
            except Exception:
                logger.debug("chat: could not resolve session agent", exc_info=True)
            if agent_pid:
                try:
                    r = async_to_sync(platform_executor.invoke)(
                        agent_pid, content,
                        {"_inline": True, "_trigger": "chat", "user_id": chat_user or "default"},
                        session_id=chat_id,
                    )
                    txt = (getattr(r, "output", "") or "").strip() or "(no response)"
                except Exception as exc:  # noqa: BLE001
                    txt = f"(agent error: {exc})"
                try:
                    gw.post(chat_id=chat_id, role="agent", sender=agent_name, content=txt)
                except Exception:
                    logger.debug("chat: posting agent reply failed", exc_info=True)
        return Response(result, status=201)
