"""Competitive Intelligence Agent — Cloud Run FastAPI wrapper."""
import asyncio, json, logging, os, sys, time
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Competitive Intelligence Agent (Dual-LLM + ForgeOS)")
_cors = os.environ.get("FORGEOS_CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in _cors], allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"], allow_headers=["Content-Type", "Authorization", "X-API-Key"])

class InvokeRequest(BaseModel):
    prompt: str

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "agent": "competitive-intel",
        "models": ["gemini-2.5-flash", "claude-opus-4-7"],
        "governance": "forgeos-http-kernel",
        "runtime_checks_per_invoke": 13,
    }

@app.post("/api/invoke")
async def invoke(req: InvokeRequest):
    from examples.competitive_intel.agent import run_competitive_intel
    return await run_competitive_intel(req.prompt)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
