"""Research Agent — Google ADK + ForgeOS HTTP Kernel."""
import asyncio, json, logging, os, time, uuid
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.WARNING)
app = FastAPI(title="Research Agent (ADK + ForgeOS)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "https://forgeos-api-meundhbn7a-ew.a.run.app")
AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "")
SYSTEM_PROMPT = "You are a research analyst. Analyze questions and return structured answers with key findings. Be concise. Use markdown."

class InvokeRequest(BaseModel):
    prompt: str

_runner = None
_session_service = None

def _get_runner():
    global _runner, _session_service
    if _runner is None:
        from google.adk import Agent, Runner
        from google.adk.sessions import InMemorySessionService
        agent = Agent(model="gemini-2.5-pro", name="research_adk", instruction=SYSTEM_PROMPT)
        _session_service = InMemorySessionService()
        _runner = Runner(agent=agent, app_name="research-adk", session_service=_session_service)
    return _runner, _session_service

@app.get("/api/health")
async def health():
    return {"status": "ok", "platform": "adk", "governance": "forgeos-http-kernel", "model": "gemini-2.5-pro"}

@app.post("/api/invoke")
async def invoke(req: InvokeRequest):
    runner, ss = _get_runner()
    session = await ss.create_session(app_name="research-adk", user_id="user-1")
    from google.genai.types import Content, Part
    msg = Content(parts=[Part(text=req.prompt)], role="user")
    output, tokens, start = "", 0, time.time()
    try:
        async for event in runner.run_async(user_id="user-1", session_id=session.id, new_message=msg):
            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        output += part.text
            if hasattr(event, "usage_metadata") and event.usage_metadata:
                tokens += getattr(event.usage_metadata, "total_token_count", 0)
    except Exception as e:
        if not output:
            return {"status": "failed", "platform": "adk", "error": str(e)}
    elapsed = time.time() - start
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{FORGEOS_URL}/api/platform/kernel/usage", json={"agent_id": AGENT_ID, "tokens_in": tokens//2, "tokens_out": tokens//2, "cost_usd": tokens*0.000001})
            await c.post(f"{FORGEOS_URL}/api/platform/agents/{AGENT_ID}/heartbeat")
    except Exception: pass
    return {"status": "completed", "platform": "adk", "output": output, "tokens": tokens, "elapsed_ms": round(elapsed*1000), "governance": {"type": "forgeos-http-kernel", "kernel_url": FORGEOS_URL}}

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
