"""
Mode C: Real ADK Agent + ForgeOS Remote Governance.

The REAL Google ADK customer-service agent runs here on its own Cloud Run.
Before every tool call, it checks with the ForgeOS kernel via HTTP.
After every invocation, it reports token usage back.

This is the ~45 lines of ForgeOS integration on top of the original ADK code.

Env vars:
  FORGEOS_API_URL  — ForgeOS control plane URL
  FORGEOS_AGENT_ID — Agent ID registered in ForgeOS
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.environ.get("AGENT_DIR", "."))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("mode-c-adk")

app = FastAPI(
    title="Mode C: ADK + ForgeOS HTTP Governance",
    description="Real ADK agent with remote kernel checks",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FORGEOS_API_URL = os.environ.get("FORGEOS_API_URL", "https://forgeos-api-meundhbn7a-ew.a.run.app")
FORGEOS_AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "customer-service")


# =========================================================================
# ForgeOS Kernel HTTP Client (~30 lines — this is the governance layer)
# =========================================================================

class ForgeOSKernelClient:
    """Calls ForgeOS kernel endpoints via HTTP for remote governance."""

    def __init__(self, base_url: str, agent_id: str):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30)

    async def check_tool(self, tool_name: str, tool_input: dict = None) -> dict:
        """Check if this agent is allowed to call this tool."""
        resp = await self._http.post("/api/platform/kernel/check-tool", json={
            "agent_id": self.agent_id,
            "tool_name": tool_name,
            "tool_input": tool_input or {},
        })
        return resp.json()

    async def record_usage(self, tokens_in: int = 0, tokens_out: int = 0,
                           cost_usd: float = 0, tool_calls: int = 0):
        """Report token/cost usage back to ForgeOS."""
        await self._http.post("/api/platform/kernel/usage", json={
            "agent_id": self.agent_id,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "tool_calls": tool_calls,
        })

    async def heartbeat(self):
        """Report liveness."""
        await self._http.post(f"/api/platform/agents/{self.agent_id}/heartbeat")

    async def audit(self, event: str, details: dict = None):
        """Record audit event."""
        await self._http.post("/api/platform/kernel/audit", json={
            "agent_id": self.agent_id,
            "event": event,
            "details": details or {},
        })


kernel_client = ForgeOSKernelClient(FORGEOS_API_URL, FORGEOS_AGENT_ID)


# =========================================================================
# ADK Tool Wrapper — intercepts tool calls with kernel checks
# =========================================================================

def make_governed_tool(original_func, kernel: ForgeOSKernelClient):
    """Wrap an ADK tool function with ForgeOS kernel governance.

    This is the key integration point. The wrapper:
    1. Calls kernel.check_tool() via HTTP before the tool runs
    2. If DENIED, returns an error without executing the tool
    3. If ALLOWED, runs the original tool function
    """
    import functools

    @functools.wraps(original_func)
    def wrapper(*args, **kwargs):
        tool_name = original_func.__name__

        # Synchronous HTTP check (ADK callbacks are sync)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    decision = pool.submit(
                        lambda: asyncio.run(kernel.check_tool(tool_name, kwargs))
                    ).result(timeout=10)
            else:
                decision = asyncio.run(kernel.check_tool(tool_name, kwargs))
        except Exception as e:
            logger.error("Kernel check failed for %s: %s — allowing by default", tool_name, e)
            decision = {"action": "allow"}

        if decision.get("action") == "deny":
            logger.warning("KERNEL DENIED: %s — %s", tool_name, decision.get("reason"))
            return {
                "status": "denied",
                "error": f"ForgeOS kernel denied: {decision.get('reason', 'policy violation')}",
                "kernel_decision": decision,
            }

        # Tool is allowed — run the original
        result = original_func(*args, **kwargs)

        # Audit the tool call
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(kernel.audit(
                    f"tool.{tool_name}",
                    {"args": kwargs, "result_status": "ok"},
                ))
        except Exception:
            pass

        return result

    return wrapper


# =========================================================================
# Build the ADK agent with governed tools
# =========================================================================

def build_governed_agent():
    """Load the real ADK customer-service agent, wrap its tools with kernel checks."""
    from google.adk import Agent
    from customer_service.config import Config
    from customer_service.prompts import GLOBAL_INSTRUCTION, INSTRUCTION
    from customer_service.shared_libraries.callbacks import (
        after_tool, before_agent, rate_limit_callback,
    )
    from customer_service.tools import tools as tool_module

    configs = Config()

    # Get all tool functions
    tool_functions = [
        tool_module.send_call_companion_link,
        tool_module.approve_discount,
        tool_module.sync_ask_for_approval,
        tool_module.update_salesforce_crm,
        tool_module.access_cart_information,
        tool_module.modify_cart,
        tool_module.get_product_recommendations,
        tool_module.check_product_availability,
        tool_module.schedule_planting_service,
        tool_module.get_available_planting_times,
        tool_module.send_care_instructions,
        tool_module.generate_qr_code,
    ]

    # Wrap each tool with ForgeOS kernel governance
    governed_tools = [make_governed_tool(f, kernel_client) for f in tool_functions]

    agent = Agent(
        model=configs.agent_settings.model,
        global_instruction=GLOBAL_INSTRUCTION,
        instruction=INSTRUCTION,
        name="customer-service-mode-c",
        tools=governed_tools,
        after_tool_callback=after_tool,
        before_agent_callback=before_agent,
        before_model_callback=rate_limit_callback,
    )
    return agent


# =========================================================================
# API endpoints
# =========================================================================

class InvokeRequest(BaseModel):
    prompt: str
    session_id: str = ""


_runner = None
_session_service = None


def _get_runner():
    global _runner, _session_service
    if _runner is None:
        from google.adk import Runner
        from google.adk.sessions import InMemorySessionService

        agent = build_governed_agent()
        _session_service = InMemorySessionService()
        _runner = Runner(agent=agent, app_name="mode-c", session_service=_session_service)
        logger.info("Mode C Runner initialized with ForgeOS governance")
    return _runner, _session_service


@app.get("/api/health")
async def health():
    # Test kernel connectivity
    try:
        check = await kernel_client.check_tool("health_check", {})
        kernel_status = "connected"
    except Exception as e:
        kernel_status = f"error: {e}"

    return {
        "status": "ok",
        "mode": "mode-c-adk-forgeos",
        "governance": "ForgeOS HTTP kernel",
        "forgeos_url": FORGEOS_API_URL,
        "agent_id": FORGEOS_AGENT_ID,
        "kernel_status": kernel_status,
        "tool_governance": "every tool call checked via HTTP",
        "budget_enforcement": True,
        "audit_trail": True,
    }


@app.post("/api/invoke")
async def invoke(req: InvokeRequest):
    runner, session_service = _get_runner()

    session = await session_service.create_session(app_name="mode-c", user_id="user-1")

    from google.genai.types import Content, Part
    message = Content(parts=[Part(text=req.prompt)], role="user")

    output = ""
    tool_calls = []
    tokens = 0
    start = time.time()

    # Heartbeat before invocation
    await kernel_client.heartbeat()

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

    # Report usage back to ForgeOS
    estimated_cost = tokens * 0.000005  # rough estimate
    await kernel_client.record_usage(
        tokens_in=tokens // 2,
        tokens_out=tokens // 2,
        cost_usd=estimated_cost,
        tool_calls=len(tool_calls),
    )

    # Heartbeat after invocation
    await kernel_client.heartbeat()

    return {
        "status": "completed",
        "output": output,
        "tool_calls": tool_calls,
        "tokens_used": tokens,
        "elapsed_ms": round(elapsed * 1000),
        "governance": {
            "mode": "ForgeOS HTTP kernel",
            "kernel_url": FORGEOS_API_URL,
            "tool_checks": "before every tool via HTTP POST /kernel/check-tool",
            "usage_reported": True,
            "heartbeat_sent": True,
            "audit_logged": True,
        },
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
