"""Proxy endpoints that forward to the ForgeOS backend (avoids CORS)."""

import httpx
from fastapi import APIRouter, Depends

from .auth import require_session
from .config import FORGEOS_API, FORGEOS_API_TOKEN

router = APIRouter(dependencies=[Depends(require_session)])


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {FORGEOS_API_TOKEN}"} if FORGEOS_API_TOKEN else {}


async def _proxy(path: str, method: str = "GET", body: dict | None = None):
    async with httpx.AsyncClient(timeout=15) as client:
        url = f"{FORGEOS_API}{path}"
        if method == "POST":
            r = await client.post(url, json=body, headers=_auth_headers())
        else:
            r = await client.get(url, headers=_auth_headers())
        try:
            return r.json()
        except Exception:
            return {"error": r.text[:200]}


@router.get("/api/platform/fleet")
async def proxy_fleet():
    return await _proxy("/api/platform/fleet")


@router.get("/api/platform/agents")
async def proxy_agents():
    return await _proxy("/api/platform/agents")


@router.get("/api/approvals")
async def proxy_approvals():
    return await _proxy("/api/approvals")


@router.get("/api/audit")
async def proxy_audit(limit: int = 50):
    return await _proxy("/api/audit")


@router.get("/api/admin/events")
async def proxy_admin_events():
    return await _proxy("/api/admin/events")


@router.get("/api/platform/kernel/contract/{agent_id}")
async def proxy_contract(agent_id: str):
    return await _proxy(f"/api/platform/kernel/contract/{agent_id}")


@router.post("/api/platform/signals/{pid}")
async def proxy_signal(pid: str, signal: str = "SIGTERM", reason: str = "operator"):
    return await _proxy(
        f"/api/platform/signals/{pid}?signal={signal}&reason={reason}", method="POST"
    )


@router.post("/api/approvals/{request_id}/approve")
async def proxy_approve(request_id: str):
    return await _proxy(f"/api/approvals/{request_id}/approve", method="POST")


@router.post("/api/approvals/{request_id}/reject")
async def proxy_reject(request_id: str):
    return await _proxy(f"/api/approvals/{request_id}/reject", method="POST")


@router.get("/api/billing/metering")
async def proxy_metering():
    return await _proxy("/api/billing/metering")


@router.post("/api/platform/agents/{agent_id}/stop")
async def proxy_stop(agent_id: str):
    return await _proxy(f"/api/platform/agents/{agent_id}/stop", method="POST")


@router.delete("/api/platform/agents/{agent_id}")
async def proxy_delete(agent_id: str):
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.delete(
            f"{FORGEOS_API}/api/platform/agents/{agent_id}", headers=_auth_headers()
        )
        try:
            return r.json()
        except Exception:
            return {"ok": r.status_code < 300}
