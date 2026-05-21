"""Proxy endpoints that forward to the ForgeOS backend (avoids CORS)."""

import httpx
from fastapi import APIRouter, Depends, Request

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


@router.get("/api/platform/mcp/servers")
async def proxy_list_mcp():
    return await _proxy("/api/platform/mcp/servers")


@router.post("/api/platform/mcp/servers")
async def proxy_add_mcp(request: Request):
    body = await request.json()
    return await _proxy("/api/platform/mcp/servers", method="POST", body=body)


@router.put("/api/platform/mcp/servers/{server_name}")
async def proxy_update_mcp(server_name: str, request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.put(
            f"{FORGEOS_API}/api/platform/mcp/servers/{server_name}",
            json=body, headers=_auth_headers(),
        )
        try:
            return r.json()
        except Exception:
            return {"error": r.text[:200]}


@router.delete("/api/platform/mcp/servers/{server_name}")
async def proxy_delete_mcp(server_name: str):
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.delete(
            f"{FORGEOS_API}/api/platform/mcp/servers/{server_name}",
            headers=_auth_headers(),
        )
        try:
            return r.json()
        except Exception:
            return {"ok": r.status_code < 300}


@router.post("/api/platform/agents/{agent_id}/invoke")
async def proxy_invoke(agent_id: str, request: Request):
    from fastapi.responses import JSONResponse
    body = await request.body()
    headers = {**_auth_headers(), "Content-Type": "application/json"}
    qs = request.url.query  # preserves ?async_mode=true
    target = f"{FORGEOS_API}/api/platform/agents/{agent_id}/invoke" + (f"?{qs}" if qs else "")
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(target, content=body, headers=headers)
        try:
            payload = r.json()
        except Exception:
            payload = {"error": r.text[:500]}
        return JSONResponse(payload, status_code=r.status_code)


@router.get("/api/platform/agents/{agent_id}/runs")
async def proxy_agent_runs(agent_id: str, limit: int = 20):
    return await _proxy(f"/api/platform/agents/{agent_id}/runs?limit={limit}")


@router.get("/api/platform/agent-logs")
async def proxy_agent_logs(request: Request):
    qs = request.url.query
    return await _proxy("/api/platform/agent-logs" + (f"?{qs}" if qs else ""))


@router.get("/api/hitl/pending")
async def proxy_hitl_pending():
    return await _proxy("/api/hitl/pending")


@router.post("/api/a2h/requests/{request_id}/approve")
async def proxy_a2h_approve(request_id: str):
    return await _proxy(f"/api/a2h/requests/{request_id}/approve", method="POST")


@router.post("/api/a2h/requests/{request_id}/reject")
async def proxy_a2h_reject(request_id: str):
    return await _proxy(f"/api/a2h/requests/{request_id}/reject", method="POST")


@router.get("/api/platform/agents/{agent_id}")
async def proxy_get_agent(agent_id: str):
    return await _proxy(f"/api/platform/agents/{agent_id}")


@router.post("/api/platform/agents/from-yaml")
async def proxy_from_yaml(request: Request):
    body = await request.body()
    headers = {**_auth_headers(), "Content-Type": "text/yaml"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{FORGEOS_API}/api/platform/agents/from-yaml",
            content=body,
            headers=headers,
        )
        try:
            return r.json()
        except Exception:
            return {"error": r.text[:500], "status": r.status_code}


@router.put("/api/platform/agents/{agent_id}/from-yaml")
async def proxy_update_yaml(agent_id: str, request: Request):
    body = await request.body()
    headers = {**_auth_headers(), "Content-Type": "text/yaml"}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.put(
            f"{FORGEOS_API}/api/platform/agents/{agent_id}/from-yaml",
            content=body,
            headers=headers,
        )
        try:
            return r.json()
        except Exception:
            return {"error": r.text[:500], "status": r.status_code}


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
