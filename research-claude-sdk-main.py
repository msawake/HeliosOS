"""Research Agent — Claude Agent SDK + ForgeOS HTTP Kernel."""
import asyncio, json, logging, os, time
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.WARNING)
app = FastAPI(title="Research Agent (Claude SDK + ForgeOS)")
_cors = os.environ.get("FORGEOS_CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in _cors], allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "Authorization", "X-API-Key"])

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "https://forgeos-api.example.com")
AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "")
SYSTEM_PROMPT = "You are a research analyst. Analyze questions and return structured answers with key findings. Be concise. Use markdown."

class InvokeRequest(BaseModel):
    prompt: str

@app.get("/api/health")
async def health():
    return {"status": "ok", "platform": "claude-agent-sdk", "governance": "forgeos-pre-tool-hook", "model": "claude-opus-4-7"}

@app.post("/api/invoke")
async def invoke(req: InvokeRequest):
    from claude_agent_sdk import query, ClaudeAgentOptions
    from claude_agent_sdk.types import AssistantMessage, ResultMessage
    options = ClaudeAgentOptions(model="claude-opus-4-7", system_prompt=SYSTEM_PROMPT, permission_mode="auto")
    output, cost, tokens_in, tokens_out = "", 0.0, 0, 0
    start = time.time()
    try:
        async for msg in query(prompt=req.prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in (msg.content or []):
                    if hasattr(block, "text"): output += block.text
            elif isinstance(msg, ResultMessage):
                cost = getattr(msg, "total_cost_usd", 0) or 0
    except Exception as e:
        return {"status": "failed", "platform": "claude-agent-sdk", "error": str(e)}
    elapsed = time.time() - start
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{FORGEOS_URL}/api/platform/kernel/usage", json={"agent_id": AGENT_ID, "cost_usd": cost})
            await c.post(f"{FORGEOS_URL}/api/platform/agents/{AGENT_ID}/heartbeat")
    except Exception: pass
    return {"status": "completed", "platform": "claude-agent-sdk", "output": output, "cost_usd": cost, "elapsed_ms": round(elapsed*1000), "governance": {"type": "forgeos-pre-tool-hook", "kernel_url": FORGEOS_URL}}

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
