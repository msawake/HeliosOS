# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
ForgeOS Python Client.

A thin, typed wrapper over the ForgeOS REST API. Lets developers manage agents
from Python without writing curl commands.

Example:
    from forgeos_sdk import ForgeOSClient, AgentManifest

    client = ForgeOSClient(base_url="http://localhost:5000", api_key="...")

    # From a YAML file
    manifest = AgentManifest.from_yaml("./agent.yaml")
    agent_id = client.deploy(manifest)

    # Invoke
    result = client.invoke(agent_id, "Check my inbox now")
    print(result["result"])
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from .manifest import AgentManifest


class ForgeOSError(Exception):
    """Raised when the API returns an error."""


class ForgeOSClient:
    """Synchronous client for the ForgeOS REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
    ):
        self.base_url = (base_url or os.environ.get("FORGEOS_API_URL", "http://localhost:5000")).rstrip("/")
        self.api_key = api_key or os.environ.get("FORGEOS_API_KEY")
        self.timeout = timeout
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._headers(),
        )

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _request(self, method: str, path: str, **kwargs) -> Any:
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise ForgeOSError(f"{method} {path} failed ({resp.status_code}): {detail}")
        if resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text

    # ---- Agent lifecycle --------------------------------------------------

    def deploy(self, manifest: AgentManifest | str | Path, base_path: Path | None = None) -> str:
        """Deploy an agent from a manifest (object or file path).

        Returns the agent_id.
        """
        if isinstance(manifest, (str, Path)):
            path = Path(manifest)
            if not base_path:
                base_path = path.parent
            if path.suffix in (".yaml", ".yml"):
                manifest = AgentManifest.from_yaml(path)
            elif path.suffix == ".json":
                manifest = AgentManifest.from_json(path)
            else:
                raise ValueError(f"Unsupported manifest file type: {path.suffix}")

        body = manifest.to_deploy_request(base_path=base_path)
        result = self._request("POST", "/api/platform/agents", json=body)
        return result["agent_id"]

    def update(self, agent_id: str, manifest: AgentManifest, base_path: Path | None = None) -> dict:
        """Update an existing agent's config."""
        body = manifest.to_deploy_request(base_path=base_path)
        return self._request("PUT", f"/api/platform/agents/{agent_id}", json=body)

    def undeploy(self, agent_id: str) -> dict:
        """Stop and remove an agent."""
        return self._request("DELETE", f"/api/platform/agents/{agent_id}")

    def stop(self, agent_id: str) -> dict:
        """Stop a running agent (loops, scheduled jobs, subscriptions)."""
        return self._request("POST", f"/api/platform/agents/{agent_id}/stop")

    # ---- Agent invocation -------------------------------------------------

    def invoke(self, agent_id: str, prompt: str, context: dict | None = None) -> dict:
        """Invoke an agent once (non-streaming). Returns AgentResult."""
        return self._request(
            "POST",
            f"/api/platform/agents/{agent_id}/invoke",
            json={"prompt": prompt, "context": context or {}},
        )

    def chat_stream(self, agent_id: str, message: str, session_id: str | None = None):
        """Stream SSE events from a chat conversation.

        Yields dicts like {"type": "text_delta", "content": "..."}.
        """
        import json as _json
        with self._http.stream(
            "POST",
            f"/api/platform/agents/{agent_id}/chat/stream",
            json={"message": message, "session_id": session_id},
        ) as resp:
            if resp.status_code >= 400:
                raise ForgeOSError(f"Chat stream failed: {resp.status_code}")
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    try:
                        yield _json.loads(line[6:])
                    except Exception:
                        pass

    # ---- Agent queries ----------------------------------------------------

    def get(self, agent_id: str) -> dict:
        """Get an agent's full definition."""
        return self._request("GET", f"/api/platform/agents/{agent_id}")

    def list(self, **filters) -> list[dict]:
        """List agents. Filter by stack, execution_type, ownership, department."""
        return self._request("GET", "/api/platform/agents", params=filters)

    def overview(self) -> dict:
        """Platform-wide agent counts and status."""
        return self._request("GET", "/api/platform/overview")

    def health(self) -> dict:
        """Platform health check."""
        return self._request("GET", "/api/health")

    # ---- Events & approvals ----------------------------------------------

    def fire_event(self, name: str, payload: dict, source: str = "sdk") -> dict:
        """Publish an event to the event bus (triggers event_driven agents)."""
        return self._request(
            "POST",
            "/api/platform/events",
            json={"name": name, "payload": payload, "source": source},
        )

    def list_approvals(self, category: str | None = None) -> list[dict]:
        """List pending HITL approvals."""
        params = {"category": category} if category else {}
        return self._request("GET", "/api/approvals", params=params)

    def approve(self, request_id: str, approved_by: str = "sdk", notes: str = "") -> dict:
        """Approve a pending HITL request."""
        return self._request(
            "POST",
            f"/api/approvals/{request_id}/approve",
            json={"approved_by": approved_by, "notes": notes},
        )

    def reject(self, request_id: str, rejected_by: str = "sdk", reason: str = "") -> dict:
        """Reject a pending HITL request."""
        return self._request(
            "POST",
            f"/api/approvals/{request_id}/reject",
            json={"rejected_by": rejected_by, "reason": reason},
        )

    # ---- Context manager --------------------------------------------------

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
