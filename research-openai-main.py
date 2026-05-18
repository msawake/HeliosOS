"""Research Agent — OpenAI Responses API + ForgeOS HTTP Kernel."""
import asyncio, json, logging, os, time
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.WARNING)
app = FastAPI(title="Research Agent (OpenAI + ForgeOS)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "https://forgeos-api-meundhbn7a-ew.a.run.app")
AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "")
SYSTEM_PROMPT = "You are a research analyst. Search the web, analyze findings, return structured answers with sources. Be concise. Use markdown."

class InvokeRequest(BaseModel):
    prompt: str

@app.get("/api/health")
async def health():
    return {"status": "ok", "platform": "openai-responses-api", "governance": "forgeos-http-kernel", "model": "gpt-4o-mini"}

@app.post("/api/invoke")
async def invoke(req: InvokeRequest):
    start = time.time()
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post("https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "input": req.prompt,
                "instructions": SYSTEM_PROMPT,
                "tools": [{"type": "web_search_preview"}],
            })
        result = r.json()

    output = ""
    tool_calls = []
    for item in result.get("output", []):
        if isinstance(item, dict):
            if item.get("type") == "message":
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        output += content.get("text", "")
            elif item.get("type") == "web_search_call":
                tool_calls.append({"name": "web_search", "status": item.get("status", "")})

    usage = result.get("usage", {})
    tokens = usage.get("total_tokens", 0) or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
    cost = (usage.get("input_tokens", 0) * 0.15 + usage.get("output_tokens", 0) * 0.60) / 1e6
    elapsed = time.time() - start

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{FORGEOS_URL}/api/platform/kernel/usage", json={"agent_id": AGENT_ID, "tokens_in": usage.get("input_tokens",0), "tokens_out": usage.get("output_tokens",0), "cost_usd": cost})
            await c.post(f"{FORGEOS_URL}/api/platform/agents/{AGENT_ID}/heartbeat")
    except Exception: pass

    return {
        "status": "completed" if output else "failed",
        "platform": "openai-responses-api",
        "output": output or result.get("error", {}).get("message", str(result)),
        "tool_calls": tool_calls,
        "tokens": tokens,
        "cost_usd": cost,
        "elapsed_ms": round(elapsed * 1000),
        "governance": {"type": "forgeos-http-kernel", "kernel_url": FORGEOS_URL},
    }

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
