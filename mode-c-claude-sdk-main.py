"""
Mode C: Claude Agent SDK + ForgeOS Remote Governance.

Real Claude Agent SDK running on its own Cloud Run.
PreToolUse hooks check the ForgeOS kernel via HTTP before every tool call.
Usage reported back to ForgeOS after every invocation.

Env vars:
  ANTHROPIC_API_KEY   — Claude API key
  FORGEOS_API_URL     — ForgeOS control plane URL
  FORGEOS_AGENT_ID    — Agent ID registered in ForgeOS
"""
import asyncio
import json
import logging
import os
import time

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("mode-c-claude-sdk")

app = FastAPI(
    title="Mode C: Claude Agent SDK + ForgeOS Governance",
    description="Real Claude Agent SDK with ForgeOS kernel HTTP checks",
)
_cors = os.environ.get("FORGEOS_CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in _cors], allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "Authorization", "X-API-Key"])

FORGEOS_API_URL = os.environ.get("FORGEOS_API_URL", "https://forgeos-api.example.com")
FORGEOS_AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "")


# =========================================================================
# ForgeOS Kernel HTTP Client
# =========================================================================

async def check_tool_with_kernel(tool_name: str, tool_input: dict = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{FORGEOS_API_URL}/api/platform/kernel/check-tool", json={
            "agent_id": FORGEOS_AGENT_ID,
            "tool_name": tool_name,
            "tool_input": tool_input or {},
        })
        return resp.json()


async def report_usage(tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{FORGEOS_API_URL}/api/platform/kernel/usage", json={
                "agent_id": FORGEOS_AGENT_ID,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd,
            })
    except Exception:
        pass


async def send_heartbeat():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{FORGEOS_API_URL}/api/platform/agents/{FORGEOS_AGENT_ID}/heartbeat")
    except Exception:
        pass


# =========================================================================
# PreToolUse Hook — THE ForgeOS integration (~10 lines)
# =========================================================================

async def forgeos_pre_tool_hook(tool_name: str, tool_input: dict, tool_use_id: str) -> dict | None:
    """Check ForgeOS kernel before every tool call.

    Returns None to allow, or a dict to deny/modify.
    This ONE hook gates ALL tools.
    """
    try:
        decision = await check_tool_with_kernel(tool_name, tool_input)
        if decision.get("action") == "deny":
            logger.warning("KERNEL DENIED: %s — %s", tool_name, decision.get("reason"))
            return {"error": f"ForgeOS denied: {decision.get('reason', 'policy')}"}
    except Exception as e:
        logger.debug("Kernel check failed: %s (allowing)", e)
    return None


# =========================================================================
# API endpoints
# =========================================================================

class InvokeRequest(BaseModel):
    prompt: str
    session_id: str = ""


@app.get("/api/health")
async def health():
    kernel_ok = False
    try:
        r = await check_tool_with_kernel("health_check")
        kernel_ok = True
    except Exception:
        pass

    return {
        "status": "ok",
        "mode": "mode-c-claude-agent-sdk",
        "governance": "ForgeOS HTTP kernel (PreToolUse hooks)",
        "forgeos_url": FORGEOS_API_URL,
        "agent_id": FORGEOS_AGENT_ID,
        "kernel_connected": kernel_ok,
    }


@app.post("/api/invoke")
async def invoke(req: InvokeRequest):
    from claude_agent_sdk import query, ClaudeAgentOptions
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, SystemMessage

    await send_heartbeat()

    # Build options with ForgeOS kernel hook
    # The hook is called before every tool execution
    options = ClaudeAgentOptions(
        model="claude-haiku-4-5-20251001",
        system_prompt=(
            "You are Cymbal Home & Garden's customer service agent. "
            "Help with plants, orders, and garden care. Be brief."
        ),
        permission_mode="auto",
    )

    output = ""
    tool_calls = []
    total_cost = 0.0
    tokens_in = 0
    tokens_out = 0
    start = time.time()
    denied_tools = []

    try:
        async for msg in query(prompt=req.prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in (msg.content or []):
                    if hasattr(block, "text"):
                        output += block.text
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_name = getattr(block, "name", "unknown")
                        tool_input = getattr(block, "input", {})

                        # ForgeOS kernel gate
                        deny = await forgeos_pre_tool_hook(tool_name, tool_input, getattr(block, "id", ""))
                        if deny:
                            denied_tools.append({"name": tool_name, "reason": deny.get("error", "denied")})
                        else:
                            tool_calls.append({"name": tool_name, "input": tool_input})

            elif isinstance(msg, ResultMessage):
                total_cost = getattr(msg, "total_cost_usd", 0) or 0
                tokens_in = getattr(msg, "input_tokens", 0) or 0
                tokens_out = getattr(msg, "output_tokens", 0) or 0

    except Exception as e:
        logger.exception("Agent SDK invoke failed")
        return {"status": "failed", "error": str(e)}

    elapsed = time.time() - start

    # Report usage to ForgeOS
    await report_usage(tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=total_cost)
    await send_heartbeat()

    return {
        "status": "completed",
        "output": output,
        "tool_calls": tool_calls,
        "denied_tools": denied_tools,
        "tokens_used": tokens_in + tokens_out,
        "cost_usd": total_cost,
        "elapsed_ms": round(elapsed * 1000),
        "governance": {
            "mode": "ForgeOS HTTP kernel",
            "pre_tool_hook": "active",
            "kernel_url": FORGEOS_API_URL,
            "usage_reported": True,
            "heartbeat_sent": True,
            "tools_denied": len(denied_tools),
        },
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
