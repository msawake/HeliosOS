"""
Standalone ADK Customer Service — No ForgeOS.

A pure Google ADK agent exposed via FastAPI. No kernel, no governance,
no budget limits, no audit trail. For comparison with the ForgeOS version.

Run locally:  python main.py
Deploy:       gcloud run deploy pure-adk-agent --source=.
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Add the customer-service agent to path
AGENT_DIR = os.environ.get("AGENT_DIR", ".")
sys.path.insert(0, AGENT_DIR)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("pure-adk")

app = FastAPI(title="Pure ADK Customer Service", description="No ForgeOS governance")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class InvokeRequest(BaseModel):
    prompt: str
    session_id: str = ""


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""


# Lazy-load ADK agent
_runner = None
_session_service = None


def _get_runner():
    global _runner, _session_service
    if _runner is None:
        from google.adk import Runner
        from google.adk.sessions import InMemorySessionService
        from customer_service.agent import root_agent

        _session_service = InMemorySessionService()
        _runner = Runner(agent=root_agent, app_name="pure-adk", session_service=_session_service)
        logger.info("ADK Runner initialized: %s", root_agent.name)
    return _runner, _session_service


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "mode": "pure-adk",
        "governance": "NONE",
        "agent": "customer-service",
        "kernel": False,
        "budget_limit": None,
        "tool_acl": "all tools allowed",
        "audit": False,
    }


@app.post("/api/invoke")
async def invoke(req: InvokeRequest):
    runner, session_service = _get_runner()
    session_id = req.session_id or str(uuid.uuid4())[:12]

    session = await session_service.create_session(
        app_name="pure-adk", user_id="user-1"
    )

    from google.genai.types import Content, Part
    message = Content(parts=[Part(text=req.prompt)], role="user")

    output = ""
    tool_calls = []
    tokens = 0
    start = time.time()

    async for event in runner.run_async(
        user_id="user-1", session_id=session.id, new_message=message
    ):
        if hasattr(event, "content") and event.content:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    output += part.text
                if hasattr(part, "function_call") and part.function_call:
                    tool_calls.append({
                        "name": part.function_call.name,
                        "args": dict(part.function_call.args) if part.function_call.args else {},
                    })
        if hasattr(event, "usage_metadata") and event.usage_metadata:
            tokens += getattr(event.usage_metadata, "total_token_count", 0)

    elapsed = time.time() - start

    return {
        "status": "completed",
        "output": output,
        "tool_calls": tool_calls,
        "tokens_used": tokens,
        "elapsed_ms": round(elapsed * 1000),
        "governance": {
            "kernel_check": "NONE",
            "budget_enforced": False,
            "audit_logged": False,
            "pii_masked": False,
        },
    }


@app.post("/api/chat")
async def chat(req: ChatRequest):
    result = await invoke(InvokeRequest(prompt=req.message, session_id=req.session_id))
    return result


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
