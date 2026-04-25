"""
A2H HTTP Transport — FastAPI implementation.

Provides the HTTP endpoints defined in the A2H specification:

    POST /a2h/v1/requests              — create request
    GET  /a2h/v1/requests/{id}         — get status
    POST /a2h/v1/requests/{id}/respond — submit response
    POST /a2h/v1/requests/{id}/cancel  — cancel request
    GET  /a2h/v1/requests              — list pending
    POST /a2h/v1/notifications         — send notification
    GET  /.well-known/participants.json — discovery

Usage:

    from a2h import Gateway
    from a2h.server import create_app

    gw = Gateway()
    app = create_app(gw)
    # Run with: uvicorn app:app
"""

from __future__ import annotations

from typing import Any


def create_app(gateway):
    """Create a FastAPI app implementing the A2H HTTP transport."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel

    app = FastAPI(
        title="A2H Protocol Server",
        description="Agent-to-Human Interaction Protocol — HTTP Transport",
        version="0.1.0",
    )

    # ---- Request models ----

    class CreateRequest(BaseModel):
        to: str
        question: str
        response_type: str = "text"
        options: list[dict] | None = None
        context: dict | None = None
        priority: str = "medium"
        deadline: str | None = None
        sla_hours: float = 24.0
        from_name: str = ""
        from_namespace: str = "default"

    class RespondRequest(BaseModel):
        response: dict
        channel: str = "dashboard"

    class CancelRequest(BaseModel):
        reason: str = ""

    class NotifyRequest(BaseModel):
        to: str
        message: str
        severity: str = "info"
        priority: str = "low"
        context: dict | None = None
        from_name: str = ""
        from_namespace: str = "default"

    # ---- Endpoints ----

    @app.post("/a2h/v1/requests", status_code=201)
    async def create_request(req: CreateRequest):
        interaction = await gateway.ask(
            to=req.to,
            question=req.question,
            response_type=req.response_type,
            options=req.options,
            context=req.context,
            priority=req.priority,
            deadline=req.deadline,
            sla_hours=req.sla_hours,
            from_name=req.from_name,
            from_namespace=req.from_namespace,
        )
        return {
            "id": interaction.id,
            "status": interaction.status.value,
            "deadline": interaction.deadline,
        }

    @app.get("/a2h/v1/requests/{interaction_id}")
    async def get_request(interaction_id: str):
        interaction = gateway.get(interaction_id)
        if not interaction:
            raise HTTPException(404, "Request not found")
        return interaction.to_dict()

    @app.post("/a2h/v1/requests/{interaction_id}/respond")
    async def respond_to_request(interaction_id: str, req: RespondRequest):
        result = gateway.respond(interaction_id, req.response, req.channel)
        if not result["success"]:
            raise HTTPException(400, result["error"])
        return result

    @app.post("/a2h/v1/requests/{interaction_id}/cancel")
    async def cancel_request(interaction_id: str, req: CancelRequest):
        result = gateway.cancel(interaction_id, req.reason)
        if not result["success"]:
            raise HTTPException(400, result["error"])
        return result

    @app.get("/a2h/v1/requests")
    async def list_requests(to: str | None = None, status: str | None = None):
        pending = gateway.list_pending(to)
        return {"requests": [i.to_dict() for i in pending]}

    @app.post("/a2h/v1/notifications", status_code=201)
    async def send_notification(req: NotifyRequest):
        notification = await gateway.notify(
            to=req.to,
            message=req.message,
            severity=req.severity,
            priority=req.priority,
            context=req.context,
            from_name=req.from_name,
            from_namespace=req.from_namespace,
        )
        return {"id": notification.id, "delivered": True}

    @app.get("/.well-known/participants.json")
    async def discovery():
        return gateway.discover()

    return app
