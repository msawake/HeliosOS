"""AgentOS Kernel governance, remote A2A, and inter-agent message endpoints.

Ported 1:1 from src/dashboard/fastapi_app.py:
  Kernel policy decisions
    - /api/platform/kernel/check-tool        POST   (3072)
    - /api/platform/kernel/check-a2a         POST   (3081)
    - /api/platform/kernel/check-data        POST   (3090)
    - /api/platform/kernel/contract/{agent_id}        GET (3097)
    - /api/platform/kernel/admit             POST   (3106)
    - /api/platform/kernel/effective-policy/{agent_id} GET (3113)
    - /api/platform/kernel/check-license     POST   (3119)
    - /api/platform/kernel/audit             POST   (3127)
  Durable policy management
    - /api/platform/kernel/namespace-policies          GET           (3145)
    - /api/platform/kernel/namespace-policy/{namespace} GET/PUT/DELETE (3151/3160/3172)
    - /api/platform/kernel/global-policy               GET/PUT        (3181/3188)
  Remote agent governance (usage + async A2A task queue)
    - /api/platform/kernel/usage             POST   (3207)
    - /api/platform/a2a/submit               POST   (3227)
    - /api/platform/a2a/jobs/{job_id}        GET    (3252)
    - /api/platform/a2a/result               POST   (3262)
    - /api/platform/a2a/fail                 POST   (3270)
    - /api/platform/a2a/tasks/pending        GET    (3278)
  Inter-agent messages
    - /api/platform/messages/{agent_id}      GET    (2354)
    - /api/platform/messages                 POST   (2367, status 201)

Platform singletons come from the process-global di.AppContext (ctx.kernel,
ctx.platform_executor, ctx.company_system, ctx.db_client).

Role gates: FastAPI applied ``Depends(require_role("admin"))`` only to the policy
mutators — PUT/DELETE namespace-policy and PUT global-policy. All other routes were
either unauthenticated (kernel/a2a/usage have no Depends) or ``Depends(check_auth)``
(POST /messages), so they rely on the global default permission.

Kernel guard: the FastAPI ``_require_kernel`` raised HTTP 503 "Kernel not
initialized". Per the porting conventions we instead return
``{"detail": "kernel unavailable"}`` with status 503 when ctx.kernel is None.

Async platform calls: the FastAPI task-queue methods (submit / get_task /
submit_result / mark_failed / get_pending_by_name) are awaited; here they run via
asgiref.async_to_sync to preserve behavior.

Task queue: FastAPI lazily attached an InMemoryTaskQueue to ``app.state`` (one per
ASGI app instance). Django has no per-app request state, so the equivalent is a
process-global lazily-initialized singleton (``_get_or_create_task_queue`` /
``_existing_task_queue``) guarded by a lock — same "first writer creates it"
semantics. NOTE: in a multi-process Django deployment this in-memory queue is
per-process (same caveat as the FastAPI version under multiple workers).
"""

from __future__ import annotations

import logging
import threading
import uuid

from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web import di
from forgeos_web.authn.permissions import require_role

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Factory-local helpers ported in (fastapi_app.py).
# ---------------------------------------------------------------------------

def _audit(action: str, **kwargs) -> None:
    """Audit stub. TODO: wire the real audit sink (fastapi_app._audit:383)."""
    logger.info("audit %s %s", action, kwargs)


def _kernel_or_503(ctx):
    """Return (kernel, None) or (None, Response) — port of fastapi_app._require_kernel
    (3067) but emitting the conventions' 503 body instead of raising HTTPException."""
    if ctx.kernel is None:
        return None, Response({"detail": "kernel unavailable"}, status=503)
    return ctx.kernel, None


def _ns_policy_store_or_resp(ctx):
    """Port of fastapi_app._require_ns_policy_store (3138).

    Returns (store, None) or (None, Response). 503 if the kernel is missing
    (conventions body) or the namespace policy store is unavailable (FastAPI body).
    """
    kernel, err = _kernel_or_503(ctx)
    if err is not None:
        return None, err
    store = getattr(kernel, "_namespace_policy_store", None)
    if store is None:
        return None, Response({"detail": "Namespace policy store not available"}, status=503)
    return store, None


# ---------------------------------------------------------------------------
# Process-global async A2A task queue (mirrors FastAPI app.state.task_queue).
# ---------------------------------------------------------------------------

_task_queue = None
_task_queue_lock = threading.Lock()


def _get_or_create_task_queue():
    """Lazily build the in-memory task queue (port of the ``app.state.task_queue``
    init at fastapi_app.py:3231)."""
    global _task_queue
    with _task_queue_lock:
        if _task_queue is None:
            from src.platform.task_queue import InMemoryTaskQueue

            _task_queue = InMemoryTaskQueue()
        return _task_queue


def _existing_task_queue():
    """Return the queue only if it has been created (mirrors the FastAPI
    ``hasattr(app.state, "task_queue")`` guard)."""
    return _task_queue


# ---------------------------------------------------------------------------
# Serializers (request bodies). Mirror the Pydantic models in fastapi_app.py.
# ---------------------------------------------------------------------------

class ToolCheckRequestSerializer(serializers.Serializer):
    """fastapi_app.ToolCheckRequest (38)."""

    agent_id = serializers.CharField()
    tool_name = serializers.CharField()
    tool_input = serializers.DictField(required=False, default=dict)
    estimated_cost_usd = serializers.FloatField(required=False, allow_null=True, default=None)


class A2ACheckRequestSerializer(serializers.Serializer):
    """fastapi_app.A2ACheckRequest (44)."""

    caller_agent_id = serializers.CharField()
    target_namespace = serializers.CharField()
    target_name = serializers.CharField()


class DataCheckRequestSerializer(serializers.Serializer):
    """fastapi_app.DataCheckRequest (49)."""

    agent_id = serializers.CharField()
    target_namespace = serializers.CharField()


class AuditRequestSerializer(serializers.Serializer):
    """fastapi_app.AuditRequest (53)."""

    agent_id = serializers.CharField()
    event = serializers.CharField()
    details = serializers.DictField(required=False, default=dict)


class UsageReportSerializer(serializers.Serializer):
    """fastapi_app.UsageReport (58)."""

    agent_id = serializers.CharField()
    tokens_in = serializers.IntegerField(required=False, default=0)
    tokens_out = serializers.IntegerField(required=False, default=0)
    cost_usd = serializers.FloatField(required=False, default=0.0)
    tool_calls = serializers.IntegerField(required=False, default=0)


class TaskSubmitRequestSerializer(serializers.Serializer):
    """fastapi_app.TaskSubmitRequest (68)."""

    caller_id = serializers.CharField()
    callee_namespace = serializers.CharField()
    callee_name = serializers.CharField()
    task = serializers.CharField()
    context = serializers.DictField(required=False, default=dict)
    timeout_seconds = serializers.FloatField(required=False, default=300)


class TaskResultRequestSerializer(serializers.Serializer):
    """fastapi_app.TaskResultRequest (76)."""

    job_id = serializers.CharField()
    result = serializers.CharField()


class TaskFailRequestSerializer(serializers.Serializer):
    """fastapi_app.TaskFailRequest (80)."""

    job_id = serializers.CharField()
    error = serializers.CharField()


class MessageSendRequestSerializer(serializers.Serializer):
    """fastapi_app.MessageSendRequest (192)."""

    from_agent_id = serializers.CharField()
    to_agent_id = serializers.CharField()
    content = serializers.DictField(required=False, default=dict)


# ---------------------------------------------------------------------------
# Kernel — policy decision endpoints.
# ---------------------------------------------------------------------------

class KernelCheckToolView(APIView):
    """POST /api/platform/kernel/check-tool — is an agent allowed to call a tool."""

    def post(self, request):
        ctx = di.get_context()
        kernel, err = _kernel_or_503(ctx)
        if err is not None:
            return err
        ser = ToolCheckRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        decision = kernel.check_tool_call(
            d["agent_id"], d["tool_name"], d["tool_input"], d["estimated_cost_usd"],
        )
        return Response(decision.to_dict())


class KernelCheckA2AView(APIView):
    """POST /api/platform/kernel/check-a2a — may caller invoke target agent."""

    def post(self, request):
        ctx = di.get_context()
        kernel, err = _kernel_or_503(ctx)
        if err is not None:
            return err
        ser = A2ACheckRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        decision = kernel.check_a2a_call(
            d["caller_agent_id"], d["target_namespace"], d["target_name"],
        )
        return Response(decision.to_dict())


class KernelCheckDataView(APIView):
    """POST /api/platform/kernel/check-data — may agent access target namespace data."""

    def post(self, request):
        ctx = di.get_context()
        kernel, err = _kernel_or_503(ctx)
        if err is not None:
            return err
        ser = DataCheckRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        decision = kernel.check_data_access(d["agent_id"], d["target_namespace"])
        return Response(decision.to_dict())


class KernelContractView(APIView):
    """GET /api/platform/kernel/contract/{agent_id} — full contract as a dict."""

    def get(self, request, agent_id):
        ctx = di.get_context()
        kernel, err = _kernel_or_503(ctx)
        if err is not None:
            return err
        contract = kernel.get_contract(agent_id)
        if contract is None:
            return Response({"detail": f"Agent {agent_id} not found"}, status=404)
        return Response(contract)


class KernelAdmitView(APIView):
    """POST /api/platform/kernel/admit — validate a contract before deploy."""

    def post(self, request):
        ctx = di.get_context()
        kernel, err = _kernel_or_503(ctx)
        if err is not None:
            return err
        # FastAPI body was a bare ``dict`` (the contract). Pass request.data through.
        result = kernel.admit(request.data)
        return Response(result.to_dict())


class KernelEffectivePolicyView(APIView):
    """GET /api/platform/kernel/effective-policy/{agent_id} — merged policy."""

    def get(self, request, agent_id):
        ctx = di.get_context()
        kernel, err = _kernel_or_503(ctx)
        if err is not None:
            return err
        return Response(kernel.effective_policy(agent_id))


class KernelCheckLicenseView(APIView):
    """POST /api/platform/kernel/check-license — is a tenant's license valid."""

    def post(self, request):
        ctx = di.get_context()
        kernel, err = _kernel_or_503(ctx)
        if err is not None:
            return err
        # FastAPI body was a bare ``dict``; tenant_id defaults to "default".
        tenant_id = (request.data or {}).get("tenant_id", "default")
        decision = kernel.check_license(tenant_id)
        return Response(decision.to_dict())


class KernelAuditView(APIView):
    """POST /api/platform/kernel/audit — record a custom audit event from an agent."""

    def post(self, request):
        ctx = di.get_context()
        kernel, err = _kernel_or_503(ctx)
        if err is not None:
            return err
        ser = AuditRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        kernel.audit(d["agent_id"], d["event"], d["details"])
        return Response({"ok": True})


# ---------------------------------------------------------------------------
# Durable policy management (namespace + global).
# ---------------------------------------------------------------------------

class NamespacePoliciesView(APIView):
    """GET /api/platform/kernel/namespace-policies — all namespace policies."""

    def get(self, request):
        ctx = di.get_context()
        store, err = _ns_policy_store_or_resp(ctx)
        if err is not None:
            return err
        return Response([p.to_dict() for p in store.list_all()])


class NamespacePolicyDetailView(APIView):
    """GET/PUT/DELETE /api/platform/kernel/namespace-policy/{namespace}.

    PUT and DELETE were ``Depends(require_role("admin"))`` in FastAPI; GET was open.
    DRF resolves permission_classes once per view, so the admin gate here applies to
    all methods. To preserve the open GET, the gate is enforced inline per method.
    """

    def get(self, request, namespace):
        ctx = di.get_context()
        store, err = _ns_policy_store_or_resp(ctx)
        if err is not None:
            return err
        policy = store.get(namespace)
        if policy is None:
            return Response({"detail": f"No policy for namespace '{namespace}'"}, status=404)
        return Response(policy.to_dict())

    def put(self, request, namespace):
        err = _require_admin(request)
        if err is not None:
            return err
        ctx = di.get_context()
        store, serr = _ns_policy_store_or_resp(ctx)
        if serr is not None:
            return serr
        from src.platform.namespace_policy import NamespacePolicy, _reconstruct

        body = request.data or {}
        policy = _reconstruct(NamespacePolicy, {**body, "namespace": namespace})
        store.apply(policy)
        _audit("policy.namespace.put", resource_type="namespace_policy",
               resource_id=namespace, details={"policy": policy.to_dict()})
        return Response({"ok": True, "namespace": namespace})

    def delete(self, request, namespace):
        err = _require_admin(request)
        if err is not None:
            return err
        ctx = di.get_context()
        store, serr = _ns_policy_store_or_resp(ctx)
        if serr is not None:
            return serr
        removed = store.delete(namespace)
        _audit("policy.namespace.delete", resource_type="namespace_policy",
               resource_id=namespace, details={"removed": removed})
        return Response({"ok": True, "removed": removed})


class GlobalPolicyView(APIView):
    """GET/PUT /api/platform/kernel/global-policy.

    PUT was ``Depends(require_role("admin"))``; GET was open. Same per-method gating
    rationale as NamespacePolicyDetailView.
    """

    def get(self, request):
        ctx = di.get_context()
        kernel, err = _kernel_or_503(ctx)
        if err is not None:
            return err
        gp = getattr(kernel, "_global_policy", None)
        return Response(gp.to_dict() if gp is not None else None)

    def put(self, request):
        err = _require_admin(request)
        if err is not None:
            return err
        ctx = di.get_context()
        kernel, kerr = _kernel_or_503(ctx)
        if kerr is not None:
            return kerr
        from src.platform.namespace_policy import GlobalPolicy, _reconstruct

        policy = _reconstruct(GlobalPolicy, request.data or {})
        gstore = getattr(kernel, "_global_policy_store", None)
        if gstore is not None:
            gstore.put(policy)
        kernel._global_policy = policy
        _audit("policy.global.put", resource_type="global_policy",
               resource_id="global", details={"policy": policy.to_dict()})
        return Response({"ok": True, "persisted": gstore is not None})


def _require_admin(request):
    """Per-method admin gate (FastAPI parity for the policy mutators).

    Returns None when allowed, or an error Response otherwise. Mirrors the
    require_role("admin") permission used as a route Depends in FastAPI, including
    its 401-vs-403 distinction (401 when unauthenticated, 403 when the role is
    insufficient). Applied inline because the GET on the same path is open, so the
    gate cannot live on the class-level permission_classes.
    """
    from forgeos_web.authn.permissions import _auth_enabled

    perm = require_role("admin")()
    if perm.has_permission(request, None):
        return None
    # No successful authenticator => unauthenticated => 401 (DRF parity);
    # authenticated but wrong role => 403.
    if _auth_enabled() and getattr(request, "auth", None) is None:
        return Response({"detail": "Not authenticated"}, status=401)
    detail = getattr(perm, "message", "Forbidden")
    return Response({"detail": detail}, status=403)


# ---------------------------------------------------------------------------
# Remote agent governance (usage reporting + async A2A task queue).
# ---------------------------------------------------------------------------

class UsageReportView(APIView):
    """POST /api/platform/kernel/usage — remote agents report token/cost usage."""

    def post(self, request):
        ctx = di.get_context()
        ser = UsageReportSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if ctx.platform_executor:
            ctx.platform_executor.process_table.record_usage(
                d["agent_id"],
                tokens_in=d["tokens_in"],
                tokens_out=d["tokens_out"],
                dollars=d["cost_usd"],
                tool_calls=d["tool_calls"],
            )
        return Response({"recorded": True, "agent_id": d["agent_id"]})


class A2ASubmitView(APIView):
    """POST /api/platform/a2a/submit — submit an async A2A task. Returns job_id."""

    def post(self, request):
        ctx = di.get_context()
        ser = TaskSubmitRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        queue = _get_or_create_task_queue()

        if ctx.kernel:
            decision = ctx.kernel.check_a2a_call(
                d["caller_id"], d["callee_namespace"], d["callee_name"],
            )
            if hasattr(decision, "denied") and decision.denied:
                return Response({"error": f"A2A denied: {decision.reason}", "allowed": False})

        job_id = async_to_sync(queue.submit)(
            caller_id=d["caller_id"],
            callee_namespace=d["callee_namespace"],
            callee_name=d["callee_name"],
            task=d["task"],
            context=d["context"],
            timeout_seconds=d["timeout_seconds"],
        )
        return Response({"job_id": job_id, "status": "pending"})


class A2AJobView(APIView):
    """GET /api/platform/a2a/jobs/{job_id} — poll for a task result."""

    def get(self, request, job_id):
        queue = _existing_task_queue()
        if queue is None:
            return Response({"detail": "No task queue"}, status=404)
        task = async_to_sync(queue.get_task)(job_id)
        if not task:
            return Response({"detail": f"Job {job_id} not found"}, status=404)
        return Response(task.to_dict())


class A2AResultView(APIView):
    """POST /api/platform/a2a/result — worker submits a completed result."""

    def post(self, request):
        ser = TaskResultRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        queue = _existing_task_queue()
        if queue is None:
            return Response({"detail": "No task queue"}, status=404)
        async_to_sync(queue.submit_result)(d["job_id"], d["result"])
        return Response({"ok": True, "job_id": d["job_id"]})


class A2AFailView(APIView):
    """POST /api/platform/a2a/fail — worker reports a task failure."""

    def post(self, request):
        ser = TaskFailRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        queue = _existing_task_queue()
        if queue is None:
            return Response({"detail": "No task queue"}, status=404)
        async_to_sync(queue.mark_failed)(d["job_id"], d["error"])
        return Response({"ok": True, "job_id": d["job_id"]})


class A2APendingTasksView(APIView):
    """GET /api/platform/a2a/tasks/pending — worker pulls pending tasks."""

    def get(self, request):
        namespace = request.query_params.get("namespace", "")
        name = request.query_params.get("name", "")
        queue = _existing_task_queue()
        if queue is None:
            return Response({"tasks": []})
        tasks = async_to_sync(queue.get_pending_by_name)(namespace, name)
        return Response({"tasks": [t.to_dict() for t in tasks]})


# ---------------------------------------------------------------------------
# Inter-agent messages.
# ---------------------------------------------------------------------------

class MessagesView(APIView):
    """GET/POST /api/platform/messages — list (per agent) is a separate path.

    POST /api/platform/messages sends an inter-agent message (FastAPI status 201,
    ``Depends(check_auth)`` -> relies on the global default permission).
    """

    def post(self, request):
        ser = MessageSendRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        # FastAPI generated an id and returned it without persisting (3370).
        msg_id = str(uuid.uuid4())[:8]
        return Response({"message_id": msg_id}, status=201)


class MessagesForAgentView(APIView):
    """GET /api/platform/messages/{agent_id} — read messages for an agent."""

    def get(self, request, agent_id):
        ctx = di.get_context()
        company_system = ctx.company_system
        if not company_system or not hasattr(company_system, "event_bus"):
            return Response([])
        try:
            if hasattr(company_system.event_bus, "get_messages"):
                unread = request.query_params.get("unread", "true").lower() != "false"
                return Response(
                    company_system.event_bus.get_messages(agent_id, unread_only=unread)
                )
        except Exception:
            pass
        return Response([])
