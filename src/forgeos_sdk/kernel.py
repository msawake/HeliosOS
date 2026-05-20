# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
# See LICENSE for full terms.
"""
SDK-side Kernel accessor.

Agents deployed with the SDK can use this class to check permissions, query
their contract, and record audit events. Works in two modes:

    1. In-process: when the agent runs inside the ForgeOS bootstrap, the
       Kernel is directly accessible — zero HTTP overhead.

    2. Remote:     when the agent runs externally (e.g. CrewAI in a Docker
       container, OpenClaw daemon, or a standalone script), the SDK proxies
       calls to the kernel over HTTP.

Both modes expose the same async API. Agent code never knows which mode it's in.

Usage:

    from forgeos_sdk import Kernel

    # Auto-detect: in-process if bootstrap is running, else remote
    kernel = Kernel.connect()

    # Or explicit
    kernel = Kernel.local()                                    # in-process
    kernel = Kernel.remote("http://forgeos:5000", "api-key")   # HTTP

    # Unified API — same in both modes
    decision = await kernel.check_tool_call("my-agent-id", "email.send", {"to": "..."})
    if decision.denied:
        raise PermissionError(decision.reason)

    contract = await kernel.get_contract("my-agent-id")
    print(contract["spec"]["boundaries"]["budgets"]["daily_usd"])

    await kernel.audit("my-agent-id", "decision_made", {"choice": "approved"})
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class KernelDecision:
    """SDK-side mirror of platform KernelDecision. Serializable over HTTP."""
    action: str
    reason: str = ""
    details: dict[str, Any] | None = None
    audit_id: str = ""
    timestamp: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    @property
    def denied(self) -> bool:
        return self.action == "deny"

    @property
    def needs_human(self) -> bool:
        return self.action == "ask_human"

    @classmethod
    def from_dict(cls, data: dict) -> "KernelDecision":
        return cls(
            action=data.get("action", "deny"),
            reason=data.get("reason", ""),
            details=data.get("details"),
            audit_id=data.get("audit_id", ""),
            timestamp=data.get("timestamp", ""),
        )


# ---------------------------------------------------------------------------
# Kernel accessors
# ---------------------------------------------------------------------------

class Kernel:
    """Unified accessor that routes to in-process or remote kernel.

    Construct via factory classmethods:
        Kernel.local()            — in-process (requires bootstrap running)
        Kernel.remote(url, key)   — HTTP
        Kernel.connect()          — auto-detect
    """

    _process_local: "Kernel | None" = None

    def __init__(self, *, backend: "_KernelBackend"):
        self._backend = backend

    # ---- Factory methods ------------------------------------------------

    @classmethod
    def local(cls, platform_kernel=None) -> "Kernel":
        """Create an in-process kernel accessor.

        If *platform_kernel* is None, uses the process-global instance set
        by bootstrap. Raises if no local kernel is available.
        """
        if platform_kernel is None:
            if cls._process_local is None:
                raise RuntimeError(
                    "No local kernel bound. Either pass platform_kernel= or "
                    "call Kernel.register_local_instance() from bootstrap."
                )
            return cls._process_local
        return cls(backend=_InProcessBackend(platform_kernel))

    @classmethod
    def remote(cls, base_url: str, api_key: str | None = None) -> "Kernel":
        """Create an HTTP-based kernel accessor."""
        return cls(backend=_HTTPBackend(base_url, api_key))

    @classmethod
    def connect(cls) -> "Kernel":
        """Auto-detect: in-process if available, else remote via env vars."""
        if cls._process_local is not None:
            return cls._process_local
        base_url = os.environ.get("FORGEOS_API_URL", "http://localhost:5000")
        api_key = os.environ.get("FORGEOS_API_KEY")
        return cls.remote(base_url, api_key)

    @classmethod
    def register_local_instance(cls, platform_kernel) -> None:
        """Bootstrap calls this once to publish the in-process kernel globally.

        After this, `Kernel.connect()` and `Kernel.local()` return the SDK
        wrapper around the given platform kernel.
        """
        cls._process_local = cls(backend=_InProcessBackend(platform_kernel))

    # ---- Unified async API ---------------------------------------------

    async def check_tool_call(
        self,
        agent_id: str,
        tool_name: str,
        tool_input: dict | None = None,
        estimated_cost_usd: float | None = None,
    ) -> KernelDecision:
        """Check if an agent is allowed to call a tool."""
        return await self._backend.check_tool_call(
            agent_id, tool_name, tool_input, estimated_cost_usd,
        )

    async def check_a2a_call(
        self,
        caller_agent_id: str,
        target_namespace: str,
        target_name: str,
    ) -> KernelDecision:
        """Check if caller may invoke target agent."""
        return await self._backend.check_a2a_call(
            caller_agent_id, target_namespace, target_name,
        )

    async def check_data_access(
        self,
        agent_id: str,
        target_namespace: str,
    ) -> KernelDecision:
        """Check if agent may access data in target namespace."""
        return await self._backend.check_data_access(agent_id, target_namespace)

    async def get_contract(self, agent_id: str) -> dict | None:
        """Return the agent's full contract as a dict (self-introspection)."""
        return await self._backend.get_contract(agent_id)

    async def admit(self, contract: dict) -> dict:
        """Validate a contract against admission policies. Returns AdmissionResult dict."""
        return await self._backend.admit(contract)

    async def audit(
        self,
        agent_id: str,
        event: str,
        details: dict | None = None,
    ) -> None:
        """Record a custom audit event from the agent."""
        await self._backend.audit(agent_id, event, details)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class _KernelBackend:
    """Abstract backend protocol."""
    async def check_tool_call(self, agent_id, tool_name, tool_input, estimated_cost_usd): ...
    async def check_a2a_call(self, caller, ns, name): ...
    async def check_data_access(self, agent_id, target_ns): ...
    async def get_contract(self, agent_id): ...
    async def admit(self, contract): ...
    async def audit(self, agent_id, event, details): ...


class _InProcessBackend(_KernelBackend):
    """Calls the platform Kernel directly — no serialization."""

    def __init__(self, platform_kernel):
        self._k = platform_kernel

    async def check_tool_call(self, agent_id, tool_name, tool_input, estimated_cost_usd):
        d = self._k.check_tool_call(agent_id, tool_name, tool_input, estimated_cost_usd)
        return KernelDecision.from_dict(d.to_dict())

    async def check_a2a_call(self, caller, ns, name):
        d = self._k.check_a2a_call(caller, ns, name)
        return KernelDecision.from_dict(d.to_dict())

    async def check_data_access(self, agent_id, target_ns):
        d = self._k.check_data_access(agent_id, target_ns)
        return KernelDecision.from_dict(d.to_dict())

    async def get_contract(self, agent_id):
        return self._k.get_contract(agent_id)

    async def admit(self, contract):
        return self._k.admit(contract).to_dict()

    async def audit(self, agent_id, event, details):
        self._k.audit(agent_id, event, details)


class _HTTPBackend(_KernelBackend):
    """Proxies kernel calls over HTTP."""

    def __init__(self, base_url: str, api_key: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._http = None

    def _get_http(self):
        if self._http is None:
            import httpx
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["X-API-Key"] = self._api_key
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=30.0,
            )
        return self._http

    async def _post(self, path: str, body: dict) -> dict:
        http = self._get_http()
        resp = await http.post(path, json=body)
        resp.raise_for_status()
        return resp.json()

    async def _get(self, path: str) -> dict:
        http = self._get_http()
        resp = await http.get(path)
        resp.raise_for_status()
        return resp.json()

    async def check_tool_call(self, agent_id, tool_name, tool_input, estimated_cost_usd):
        data = await self._post(
            "/api/platform/kernel/check-tool",
            {
                "agent_id": agent_id,
                "tool_name": tool_name,
                "tool_input": tool_input or {},
                "estimated_cost_usd": estimated_cost_usd,
            },
        )
        return KernelDecision.from_dict(data)

    async def check_a2a_call(self, caller, ns, name):
        data = await self._post(
            "/api/platform/kernel/check-a2a",
            {"caller_agent_id": caller, "target_namespace": ns, "target_name": name},
        )
        return KernelDecision.from_dict(data)

    async def check_data_access(self, agent_id, target_ns):
        data = await self._post(
            "/api/platform/kernel/check-data",
            {"agent_id": agent_id, "target_namespace": target_ns},
        )
        return KernelDecision.from_dict(data)

    async def get_contract(self, agent_id):
        return await self._get(f"/api/platform/kernel/contract/{agent_id}")

    async def admit(self, contract):
        return await self._post("/api/platform/kernel/admit", contract)

    async def audit(self, agent_id, event, details):
        await self._post(
            "/api/platform/kernel/audit",
            {"agent_id": agent_id, "event": event, "details": details or {}},
        )

    async def record_usage(self, agent_id, tokens_in=0, tokens_out=0, cost_usd=0.0, tool_calls=0):
        await self._post(
            "/api/platform/kernel/usage",
            {"agent_id": agent_id, "tokens_in": tokens_in, "tokens_out": tokens_out,
             "cost_usd": cost_usd, "tool_calls": tool_calls},
        )

    async def heartbeat(self, agent_id):
        await self._post(f"/api/platform/agents/{agent_id}/heartbeat", {})

    async def submit_task(self, caller_id, callee_namespace, callee_name,
                          task, context=None, timeout_seconds=300):
        data = await self._post("/api/platform/a2a/submit", {
            "caller_id": caller_id, "callee_namespace": callee_namespace,
            "callee_name": callee_name, "task": task,
            "context": context or {}, "timeout_seconds": timeout_seconds,
        })
        return data.get("job_id")

    async def get_task_result(self, job_id):
        return await self._get(f"/api/platform/a2a/jobs/{job_id}")

    async def submit_result(self, job_id, result):
        await self._post("/api/platform/a2a/result", {"job_id": job_id, "result": result})

    async def get_pending_tasks(self, namespace, name):
        return await self._get(f"/api/platform/a2a/tasks/pending?namespace={namespace}&name={name}")
