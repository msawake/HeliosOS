"""Research Agent Comparison — 4 platforms + explainer pages."""
import asyncio, json, logging, os, time
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.WARNING)
app = FastAPI(title="ForgeOS Research Agent — 4-Platform Comparison")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "https://forgeos-api-meundhbn7a-ew.a.run.app")
ADK_URL = os.environ.get("RESEARCH_ADK_URL", "")
CLAUDE_SDK_URL = os.environ.get("RESEARCH_CLAUDE_SDK_URL", "")
OPENAI_URL = os.environ.get("RESEARCH_OPENAI_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_managed_agent_id = None
_managed_env_id = None

class CompareRequest(BaseModel):
    prompt: str
    platforms: list[str] = ["forgeos", "adk", "claude-sdk", "openai", "managed"]

# =========================================================================
# Platform callers
# =========================================================================

async def _call_forgeos(prompt):
    async with httpx.AsyncClient(timeout=120) as c:
        agents = (await c.get(f"{FORGEOS_URL}/api/platform/agents")).json()
        aid = next((a["agent_id"] for a in agents if "research" in a["name"] and "forgeos" in a["name"]), None)
        if not aid: return {"platform": "forgeos", "error": "research-agent-forgeos not deployed"}
        start = time.time()
        r = (await c.post(f"{FORGEOS_URL}/api/platform/agents/{aid}/invoke", json={"prompt": prompt})).json()
        return {"platform": "forgeos", "runtime": "ForgeOS Native", "model": "Gemini 2.5 Pro", "governance": "In-process kernel (~0.1ms)", "output": r.get("result") or r.get("output") or r.get("error",""), "tokens": r.get("tokens_used",0), "elapsed_ms": round((time.time()-start)*1000)}

async def _call_adk(prompt):
    if not ADK_URL: return {"platform": "adk", "error": "Not configured"}
    async with httpx.AsyncClient(timeout=120) as c:
        start = time.time()
        r = (await c.post(f"{ADK_URL}/api/invoke", json={"prompt": prompt})).json()
        return {"platform": "adk", "runtime": "Google ADK Runner", "model": "Gemini 2.5 Pro", "governance": "ForgeOS HTTP kernel (~50ms)", "output": r.get("output",""), "tokens": r.get("tokens",0), "elapsed_ms": round((time.time()-start)*1000)}

async def _call_claude_sdk(prompt):
    if not CLAUDE_SDK_URL: return {"platform": "claude-sdk", "error": "Not configured"}
    async with httpx.AsyncClient(timeout=120) as c:
        start = time.time()
        r = (await c.post(f"{CLAUDE_SDK_URL}/api/invoke", json={"prompt": prompt})).json()
        return {"platform": "claude-sdk", "runtime": "Claude Agent SDK", "model": "Claude Opus 4.7", "governance": "PreToolUse hook + HTTP kernel", "output": r.get("output",""), "cost_usd": r.get("cost_usd",0), "elapsed_ms": round((time.time()-start)*1000)}

async def _call_openai(prompt):
    if not OPENAI_URL: return {"platform": "openai", "error": "Not configured"}
    async with httpx.AsyncClient(timeout=120) as c:
        start = time.time()
        r = (await c.post(f"{OPENAI_URL}/api/invoke", json={"prompt": prompt})).json()
        return {"platform": "openai", "runtime": "OpenAI Responses API", "model": "GPT-4o Mini", "governance": "ForgeOS HTTP kernel", "output": r.get("output",""), "tokens": r.get("tokens",0), "cost_usd": r.get("cost_usd",0), "elapsed_ms": round((time.time()-start)*1000)}

async def _call_managed(prompt):
    global _managed_agent_id, _managed_env_id
    if not ANTHROPIC_API_KEY: return {"platform": "managed", "error": "Not configured"}
    headers = {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "anthropic-beta": "managed-agents-2026-04-01", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=120) as c:
        if not _managed_agent_id:
            r = (await c.post("https://api.anthropic.com/v1/agents", headers=headers, json={"model": "claude-opus-4-7", "name": "research-managed", "system": "You are a research analyst. Be concise. Use markdown.", "tools": [{"type": "agent_toolset_20260401"}]})).json()
            _managed_agent_id = r.get("id")
        if not _managed_env_id:
            r = (await c.post("https://api.anthropic.com/v1/environments", headers=headers, json={"name": "research-env"})).json()
            _managed_env_id = r.get("id")
        if not _managed_agent_id or not _managed_env_id:
            return {"platform": "managed", "error": "Failed to create managed agent"}
        start = time.time()
        session = (await c.post("https://api.anthropic.com/v1/sessions", headers=headers, json={"agent": _managed_agent_id, "environment_id": _managed_env_id})).json()
        sid = session.get("id")
        await c.post(f"https://api.anthropic.com/v1/sessions/{sid}/events", headers=headers, json={"events": [{"type": "user.message", "content": [{"type": "text", "text": prompt}]}]})
        output = ""
        for _ in range(30):
            await asyncio.sleep(3)
            s = (await c.get(f"https://api.anthropic.com/v1/sessions/{sid}", headers=headers)).json()
            if s.get("status") == "idle":
                events = (await c.get(f"https://api.anthropic.com/v1/sessions/{sid}/events?limit=50", headers=headers)).json()
                for e in events.get("data", []):
                    if e.get("type") == "agent.message":
                        for b in e.get("content", []):
                            if b.get("type") == "text": output += b["text"]
                usage = s.get("usage", {})
                return {"platform": "managed", "runtime": "Anthropic Managed Agents", "model": "Claude Opus 4.7", "governance": "ForgeOS at session level", "output": output, "tokens": usage.get("input_tokens",0)+usage.get("output_tokens",0), "cost_usd": (usage.get("input_tokens",0)*15+usage.get("output_tokens",0)*75)/1e6, "elapsed_ms": round((time.time()-start)*1000)}
        return {"platform": "managed", "error": "Timeout"}

# =========================================================================
# Shared HTML helpers
# =========================================================================

CSS = """
*{box-sizing:border-box}body{font-family:'Inter',system-ui,sans-serif;max-width:1200px;margin:0 auto;padding:0 20px 40px;background:#0d0d0d;color:#e5e5e5}
nav{display:flex;gap:4px;padding:16px 0;border-bottom:1px solid #222;margin-bottom:24px}
nav a{color:#8e8ea0;text-decoration:none;padding:8px 16px;border-radius:8px;font-size:14px;transition:all .15s}
nav a:hover{background:#1a1a2e;color:#fff}nav a.active{background:#10A37F;color:#fff}
h1{color:#10A37F;font-size:28px;margin:0 0 4px}h2{color:#10A37F;font-size:22px;margin:32px 0 12px}h3{color:#e5e5e5;font-size:16px;margin:20px 0 8px}
p,.desc{color:#8e8ea0;font-size:14px;line-height:1.6;margin:0 0 16px}
.subtitle{color:#8e8ea0;font-size:14px;margin:0 0 24px}
.card{background:#1a1a2e;border:1px solid #2a2a3e;border-radius:12px;padding:20px;margin-bottom:16px}
.card h3{color:#10A37F;margin:0 0 8px;font-size:15px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.grid4{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px}
@media(max-width:800px){.grid2,.grid4{grid-template-columns:1fr}}
pre,code{background:#111;border:1px solid #333;border-radius:6px;padding:12px;font-size:12px;overflow-x:auto;color:#a0d0b0}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;margin-right:4px}
.tag-green{background:#10A37F22;color:#10A37F}.tag-blue{background:#3b82f622;color:#60a5fa}
.tag-purple{background:#8b5cf622;color:#a78bfa}.tag-orange{background:#f59e0b22;color:#fbbf24}
.tag-red{background:#ef444422;color:#f87171}
.step{display:flex;gap:12px;margin-bottom:12px;align-items:flex-start}
.step-num{background:#10A37F;color:#fff;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0}
.step-text{font-size:13px;line-height:1.5}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:#8e8ea0;padding:8px;border-bottom:1px solid #333}
td{padding:8px;border-bottom:1px solid #1a1a2e}
textarea{width:100%;height:80px;background:#1a1a2e;color:#e5e5e5;border:1px solid #333;border-radius:8px;padding:12px;font-size:14px;resize:vertical}
button{background:#10A37F;color:white;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-size:14px;margin-top:8px}
button:hover{background:#0d8c6d}button:disabled{opacity:0.5}
.output{white-space:pre-wrap;font-size:13px;line-height:1.5;max-height:400px;overflow-y:auto}
.meta{color:#666;font-size:12px;margin-bottom:8px}
.loading{text-align:center;padding:40px;color:#888}
.badge{font-size:11px;padding:3px 8px;border-radius:4px;font-weight:600}
.badge-running{background:#10A37F33;color:#10A37F}.badge-failed{background:#ef444433;color:#f87171}
"""

def _nav(active):
    items = [("compare","/","Compare"),("demos","/demos","Demos"),("arch","/architecture","Architecture"),("gov","/governance","Governance"),("flow","/flow","Request Flow"),("fleet","/fleet","Fleet Status")]
    links = "".join(f'<a href="{href}" class="{"active" if active==key else ""}">{label}</a>' for key,href,label in items)
    return f'<nav>{links}</nav>'

def _page(active, title, subtitle, body):
    return f"""<!DOCTYPE html><html><head><title>{title} — ForgeOS</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{CSS}</style></head><body>{_nav(active)}
<h1>{title}</h1><p class="subtitle">{subtitle}</p>{body}</body></html>"""

# =========================================================================
# API
# =========================================================================

@app.get("/api/health")
async def health():
    return {"status": "ok", "platforms": ["forgeos","adk","claude-sdk","managed"]}

class ToolCheckRequest(BaseModel):
    tool_name: str

@app.post("/api/kernel/check-tool")
async def proxy_kernel_check(req: ToolCheckRequest):
    """Proxy kernel check to ForgeOS (avoids CORS issues from browser)."""
    async with httpx.AsyncClient(timeout=30) as c:
        agents = (await c.get(f"{FORGEOS_URL}/api/platform/agents")).json()
        agent = next((a for a in agents if "research" in a["name"]), None)
        if not agent:
            return {"error": "No research agent deployed", "action": "error"}
        r = await c.post(f"{FORGEOS_URL}/api/platform/kernel/check-tool", json={
            "agent_id": agent["agent_id"], "tool_name": req.tool_name, "tool_input": {},
        })
        return r.json()

@app.get("/api/fleet")
async def proxy_fleet():
    """Proxy fleet status from ForgeOS (avoids CORS issues from browser)."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{FORGEOS_URL}/api/platform/fleet")
        return r.json()

@app.post("/api/compare")
async def compare(req: CompareRequest):
    dispatch = {"forgeos": _call_forgeos, "adk": _call_adk, "claude-sdk": _call_claude_sdk, "openai": _call_openai, "managed": _call_managed}
    results = await asyncio.gather(*[dispatch[p](req.prompt) for p in req.platforms if p in dispatch], return_exceptions=True)
    out = {}
    for r in results:
        if isinstance(r, dict): out[r.get("platform","?")] = r
        elif isinstance(r, Exception): out["error"] = str(r)
    return {"prompt": req.prompt, "results": out}

# =========================================================================
# Page 1: Compare (Home)
# =========================================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    return _page("compare", "Research Agent Comparison",
        "Same prompt sent to 5 different AI platforms in parallel. ForgeOS governs all of them.",
        """
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px">
  <div class="card"><h3>ForgeOS Native</h3><p>Gemini 2.5 Pro<br><span class="tag tag-green">In-process kernel</span></p></div>
  <div class="card"><h3>Google ADK</h3><p>Gemini 2.5 Pro<br><span class="tag tag-blue">HTTP kernel</span></p></div>
  <div class="card"><h3>Claude Agent SDK</h3><p>Claude Opus 4.7<br><span class="tag tag-purple">PreToolUse hook</span></p></div>
  <div class="card"><h3>OpenAI Responses</h3><p>GPT-4o Mini<br><span class="tag tag-blue">HTTP kernel</span></p></div>
  <div class="card"><h3>Anthropic Managed</h3><p>Claude Opus 4.7<br><span class="tag tag-orange">Session-level</span></p></div>
</div>
<textarea id="prompt" placeholder="Enter your research question...">What are the most important breakthroughs in AI in 2026?</textarea>
<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
<button onclick="run()" id="btn">Compare All 5 Platforms</button>
<button onclick="run(['forgeos','adk'])" style="background:#333">Gemini Only</button>
<button onclick="run(['claude-sdk','managed'])" style="background:#333">Claude Only</button>
<button onclick="run(['forgeos','claude-sdk','openai'])" style="background:#333">All 3 Providers</button>
</div>
<div id="results"></div>
<script>
async function run(platforms){
  const btn=document.getElementById('btn');btn.disabled=true;btn.textContent='Running...';
  const body={prompt:document.getElementById('prompt').value};
  if(platforms)body.platforms=platforms;
  document.getElementById('results').innerHTML='<div class="loading">Sending prompt to '+(platforms?platforms.join(', '):'all 5 platforms')+'...</div>';
  try{
    const r=await fetch('/api/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();let html='<div class="grid2" style="margin-top:20px">';
    const order=['forgeos','adk','claude-sdk','openai','managed'];
    for(const k of order){const v=d.results[k];if(!v)continue;
      const tags={'forgeos':'tag-green','adk':'tag-blue','claude-sdk':'tag-purple','managed':'tag-orange'};
      html+=`<div class="card"><h3>${v.platform||k} <span class="tag ${tags[k]||''}">${v.governance||''}</span></h3>
      <div class="meta">${v.runtime||''} | ${v.model||''}</div>
      <div class="meta">Tokens: ${v.tokens||'?'} | Cost: $${(v.cost_usd||0).toFixed(4)} | ${v.elapsed_ms||'?'}ms</div>
      <div class="output">${(v.output||v.error||'No output').replace(/</g,'&lt;').replace(/\\n/g,'\\n')}</div></div>`;}
    document.getElementById('results').innerHTML=html+'</div>';
  }catch(e){document.getElementById('results').innerHTML='<div class="card">Error: '+e+'</div>';}
  btn.disabled=false;btn.textContent='Compare All 5 Platforms';
}
</script>""")

# =========================================================================
# Page: Demos
# =========================================================================

@app.get("/demos", response_class=HTMLResponse)
async def demos():
    return _page("demos", "Demo Scenarios",
        "Pre-built prompts that showcase different aspects of the platform comparison. Click any demo to run it.",
        """
<div class="grid2">

<div class="card">
  <h3>Research Quality</h3>
  <p>Compare how each model analyzes a complex topic.</p>
  <button onclick="runDemo('What are the 3 most promising approaches to achieving AGI? For each, give the strongest argument for and against in 2 sentences.')">Run: AGI Approaches</button>
  <button onclick="runDemo('Compare quantum computing vs classical computing for AI training. Which will dominate by 2030 and why?')" style="background:#333;margin-top:4px">Run: Quantum vs Classical</button>
  <button onclick="runDemo('What lessons from biology could help us build better AI systems? Give 3 specific examples with scientific references.')" style="background:#333;margin-top:4px">Run: Bio-Inspired AI</button>
</div>

<div class="card">
  <h3>Business Analysis</h3>
  <p>Test strategic reasoning across different models.</p>
  <button onclick="runDemo('Analyze the competitive landscape of AI agent platforms in 2026. Who are the top 5 players and what differentiates them?')">Run: Market Analysis</button>
  <button onclick="runDemo('A company has 200 AI agents running across 6 departments. What governance framework should they implement? Give specific controls.')" style="background:#333;margin-top:4px">Run: AI Governance</button>
  <button onclick="runDemo('What is the ROI of deploying AI agents for customer service vs human agents? Calculate for a company with 50 support staff.')" style="background:#333;margin-top:4px">Run: ROI Calculation</button>
</div>

<div class="card">
  <h3>Technical Deep-Dive</h3>
  <p>Test technical accuracy and depth.</p>
  <button onclick="runDemo('Explain the transformer attention mechanism in 3 levels: for a CEO, for a software engineer, and for an ML researcher.')">Run: Transformer Explainer</button>
  <button onclick="runDemo('Design a distributed system architecture for running 1000 AI agents across multiple cloud regions. Include failure modes and recovery.')" style="background:#333;margin-top:4px">Run: System Design</button>
  <button onclick="runDemo('What are the security risks of autonomous AI agents? List the top 5 attack vectors with mitigation strategies.')" style="background:#333;margin-top:4px">Run: Agent Security</button>
</div>

<div class="card">
  <h3>Creative & Reasoning</h3>
  <p>Test creative and logical reasoning.</p>
  <button onclick="runDemo('Write a 1-paragraph sci-fi scenario set in 2035 where AI agents have their own economy. Make it thought-provoking.')">Run: Sci-Fi Scenario</button>
  <button onclick="runDemo('A trolley is heading toward 5 AI agents. You can divert it to hit 1 human. What are the ethical considerations? Be rigorous.')" style="background:#333;margin-top:4px">Run: Ethics Puzzle</button>
  <button onclick="runDemo('If you could add one feature to every AI model that would have the most positive impact on humanity, what would it be and why?')" style="background:#333;margin-top:4px">Run: One Feature</button>
</div>

<div class="card">
  <h3>Speed Test</h3>
  <p>Minimal prompt to compare raw response times.</p>
  <button onclick="runDemo('Hello. Respond in exactly one sentence.')">Run: One Sentence</button>
  <button onclick="runDemo('List 3 colors.')" style="background:#333;margin-top:4px">Run: 3 Colors</button>
  <button onclick="runDemo('What is 2+2?')" style="background:#333;margin-top:4px">Run: Math</button>
</div>

<div class="card">
  <h3>Provider Comparison</h3>
  <p>Run specific subsets to compare providers.</p>
  <button onclick="runDemoSubset('Which is better for coding: Claude or GPT? Give 3 specific comparisons.',['claude-sdk','openai'])">Run: Claude vs GPT</button>
  <button onclick="runDemoSubset('What are the pros and cons of Google Gemini vs Claude for enterprise?',['forgeos','claude-sdk'])" style="background:#333;margin-top:4px">Run: Gemini vs Claude</button>
  <button onclick="runDemoSubset('Compare all 3 major AI providers for agent development.',['forgeos','claude-sdk','openai'])" style="background:#333;margin-top:4px">Run: All 3 Providers</button>
</div>

</div>

<div id="demo-results" style="margin-top:20px"></div>

<script>
async function runDemo(prompt){
  document.getElementById('demo-results').innerHTML='<div class="loading">Running all 5 platforms...</div>';
  try{
    const r=await fetch('/api/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt})});
    const d=await r.json();
    renderResults(d);
  }catch(e){document.getElementById('demo-results').innerHTML='<div class="card">Error: '+e+'</div>';}
}
async function runDemoSubset(prompt,platforms){
  document.getElementById('demo-results').innerHTML='<div class="loading">Running '+platforms.join(', ')+'...</div>';
  try{
    const r=await fetch('/api/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt,platforms})});
    const d=await r.json();
    renderResults(d);
  }catch(e){document.getElementById('demo-results').innerHTML='<div class="card">Error: '+e+'</div>';}
}
function renderResults(d){
  const tags={'forgeos':'tag-green','adk':'tag-blue','claude-sdk':'tag-purple','openai':'tag-blue','managed':'tag-orange'};
  const order=['forgeos','adk','claude-sdk','openai','managed'];
  let html='<h2 style="color:#10A37F">Results</h2><p class="meta">Prompt: "'+d.prompt.substring(0,100)+'..."</p><div class="grid2">';
  for(const k of order){const v=d.results[k];if(!v)continue;
    html+=`<div class="card"><h3>${v.platform||k} <span class="tag ${tags[k]||''}">${v.governance||''}</span></h3>
    <div class="meta">${v.runtime||''} | ${v.model||''}</div>
    <div class="meta">Tokens: ${v.tokens||'?'} | Cost: $${(v.cost_usd||0).toFixed(4)} | ${v.elapsed_ms||'?'}ms</div>
    <div class="output">${(v.output||v.error||'No output').replace(/</g,'&lt;')}</div></div>`;}
  document.getElementById('demo-results').innerHTML=html+'</div>';
}
</script>
""")

# =========================================================================
# Page 2: Architecture
# =========================================================================

@app.get("/architecture", response_class=HTMLResponse)
async def architecture():
    return _page("arch", "Architecture",
        "How ForgeOS governs agents across 4 different platforms on Google Cloud Run.",
        """
<div class="card">
<h3>System Overview</h3>
<pre style="font-size:11px;line-height:1.4;color:#a0d0b0">
                        USER (Browser)
                             |
                    POST /api/compare
                             |
                +-----------------------+
                |  Comparison Service   |
                |  (Cloud Run)          |
                +-----------+-----------+
                            |
          +-----------------+------------------+------------------+
          |                 |                  |                  |
  +-------v-------+ +------v--------+ +------v--------+ +------v--------+
  | ForgeOS API   | | ADK Service   | | Claude SDK    | | Anthropic     |
  | (Cloud Run)   | | (Cloud Run)   | | (Cloud Run)   | | Managed       |
  |               | |               | |               | | (Anthropic    |
  | Gemini 2.5 Pro| | Gemini 2.5 Pro| | Opus 4.7      | |  hosted)      |
  | KERNEL INSIDE | | HTTP to kernel| | Hook to kernel| | Session-level |
  +-------+-------+ +------+--------+ +------+--------+ +------+--------+
          |                 |                  |                  |
          +--------+--------+--------+---------+                  |
                   |                                              |
           +-------v-----------+                                  |
           |  ForgeOS KERNEL   |&lt;---------------------------------+
           |  (same process    |    Session-level governance only
           |   as ForgeOS API) |
           |                   |
           |  - PermissionMgr  |  Check tool ACLs
           |  - BudgetMgr      |  Enforce spend limits
           |  - PolicyEngine   |  Evaluate rules
           |  - AuditLog       |  Record every decision
           |  - ProcessTable   |  Track all agents
           +-------------------+
</pre>
</div>

<h2>4 Governance Modes</h2>
<div class="grid2">
  <div class="card">
    <h3><span class="tag tag-green">Mode A</span> ForgeOS Native</h3>
    <p>Agent runs inside ForgeOS. Kernel is in the same Python process.</p>
    <p><strong>Latency:</strong> ~0.1ms per tool check (direct function call)</p>
    <p><strong>Control:</strong> Full — every tool call gated before execution</p>
  </div>
  <div class="card">
    <h3><span class="tag tag-blue">Mode B</span> ADK + HTTP Kernel</h3>
    <p>ADK agent runs on separate Cloud Run. Calls ForgeOS kernel via HTTP before each tool.</p>
    <p><strong>Latency:</strong> ~50ms per check (network round-trip)</p>
    <p><strong>Control:</strong> Full — tool wrapper checks kernel for every call</p>
  </div>
  <div class="card">
    <h3><span class="tag tag-purple">Mode C</span> Claude SDK + PreToolUse Hook</h3>
    <p>Claude Agent SDK runs on separate Cloud Run. One PreToolUse hook gates ALL tools via HTTP.</p>
    <p><strong>Latency:</strong> ~50ms per check (same HTTP, cleaner integration)</p>
    <p><strong>Control:</strong> Full — Anthropic SDK calls hook before every tool</p>
  </div>
  <div class="card">
    <h3><span class="tag tag-orange">Mode D</span> Managed Agents</h3>
    <p>Agent runs in Anthropic's hosted sandbox. ForgeOS only controls at session creation.</p>
    <p><strong>Latency:</strong> N/A (no per-tool interception)</p>
    <p><strong>Control:</strong> Limited — budget/ACL checked before session, not per tool</p>
  </div>
</div>

<h2>Cloud Run Services</h2>
<table>
  <tr><th>Service</th><th>Image</th><th>Model</th><th>Governance</th></tr>
  <tr><td>forgeos-api</td><td>forgeos/api</td><td>Gemini 2.5 Pro (Vertex AI)</td><td>In-process kernel</td></tr>
  <tr><td>research-adk</td><td>forgeos/research-adk</td><td>Gemini 2.5 Pro (Vertex AI)</td><td>HTTP kernel</td></tr>
  <tr><td>research-claude-sdk</td><td>forgeos/research-claude</td><td>Claude Opus 4.7</td><td>PreToolUse hook</td></tr>
  <tr><td>research-compare</td><td>forgeos/research-compare</td><td>—</td><td>Orchestrator</td></tr>
</table>
""")

# =========================================================================
# Page 3: Governance
# =========================================================================

@app.get("/governance", response_class=HTMLResponse)
async def governance():
    return _page("gov", "Governance",
        "What ForgeOS controls — from a YAML manifest to runtime enforcement.",
        """
<h2>The Manifest → Kernel Pipeline</h2>
<p>Every agent is defined by a YAML manifest. The kernel reads it and enforces rules at runtime.</p>

<div class="grid2">
<div class="card">
<h3>Agent Manifest (YAML)</h3>
<pre>apiVersion: forgeos/v1
kind: Agent
metadata:
  name: research-agent
  namespace: research
spec:
  stack: forgeos
  llm:
    chat_model: gemini-2.5-pro
  capabilities:
    tools:
      allowed:
        - company__search_knowledge
        - memory__*
      denied:
        - approve_discount
        - delete_*
  boundaries:
    budgets:
      daily_usd: 5.00
      per_task_usd: 0.50
    data:
      pii_policy: mask
  governance:
    audit_level: full</pre>
</div>
<div class="card">
<h3>What the Kernel Enforces</h3>
<div class="step"><div class="step-num">1</div><div class="step-text"><strong>Tool ACL</strong><br>Agent tries <code>approve_discount</code> → Kernel checks <code>capabilities.tools.denied</code> → <span class="tag tag-red">DENIED</span></div></div>
<div class="step"><div class="step-num">2</div><div class="step-text"><strong>Budget</strong><br>Agent spent $4.80 today → limit is $5.00 → next tool costs ~$0.30 → <span class="tag tag-red">DENIED</span> (would exceed)</div></div>
<div class="step"><div class="step-num">3</div><div class="step-text"><strong>PII Masking</strong><br>Customer email appears in output → <code>pii_policy: mask</code> → email redacted in audit log</div></div>
<div class="step"><div class="step-num">4</div><div class="step-text"><strong>Audit Trail</strong><br>Every kernel decision logged with agent_id, tool_name, action, timestamp → hash-chained (tamper-proof)</div></div>
<div class="step"><div class="step-num">5</div><div class="step-text"><strong>Namespace Isolation</strong><br>Agent in <code>research</code> namespace tries to access <code>finance</code> data → <span class="tag tag-red">DENIED</span></div></div>
</div>
</div>

<h2>Try It Live</h2>
<p>Click to check if the research agent can call a specific tool:</p>
<div class="grid2">
  <div class="card">
    <button onclick="checkTool('company__search_knowledge')">Check: company__search_knowledge</button>
    <button onclick="checkTool('approve_discount')" style="background:#333">Check: approve_discount</button>
    <button onclick="checkTool('memory__read')">Check: memory__read</button>
    <button onclick="checkTool('delete_all_data')" style="background:#333">Check: delete_all_data</button>
    <pre id="kernel-result" style="margin-top:12px;min-height:60px">Click a button to test...</pre>
  </div>
  <div class="card">
    <h3>What happens</h3>
    <p>Each button calls <code>POST /api/platform/kernel/check-tool</code> on the live ForgeOS server. The kernel checks the manifest's <code>allowed</code> and <code>denied</code> lists and returns ALLOW or DENY.</p>
    <p>The same check runs before <strong>every tool call</strong> at runtime — the agent never sees denied tools.</p>
  </div>
</div>
<script>
async function checkTool(name){
  document.getElementById('kernel-result').textContent='Checking...';
  try{
    const r=await fetch('/api/kernel/check-tool',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool_name:name})});
    const d=await r.json();
    document.getElementById('kernel-result').textContent=JSON.stringify(d,null,2);
  }catch(e){document.getElementById('kernel-result').textContent='Error: '+e;}
}
</script>
""")

# =========================================================================
# Page 4: Request Flow
# =========================================================================

@app.get("/flow", response_class=HTMLResponse)
async def flow():
    return _page("flow", "Request Flow",
        "Step-by-step: what happens when you send a prompt to each platform.",
        """
<h2>The 8-Step Journey</h2>
<p>Every agent invocation follows these steps. The difference is <em>where</em> governance happens.</p>

<div class="card">
<div class="step"><div class="step-num">1</div><div class="step-text"><strong>User types prompt</strong><br>"What are the top AI trends?" → browser sends POST request</div></div>
<div class="step"><div class="step-num">2</div><div class="step-text"><strong>Request arrives at Cloud Run</strong><br>The service receives the HTTP request (ForgeOS, ADK, Claude SDK, or Managed)</div></div>
<div class="step"><div class="step-num">3</div><div class="step-text"><strong>ForgeOS kernel checks</strong> <span class="tag tag-green">governance</span><br>Budget within limit? Agent allowed? Namespace correct? → ALLOW or DENY</div></div>
<div class="step"><div class="step-num">4</div><div class="step-text"><strong>LLM called</strong><br>Gemini 2.5 Pro (Vertex AI) or Claude Opus 4.7 (Anthropic API) processes the prompt</div></div>
<div class="step"><div class="step-num">5</div><div class="step-text"><strong>LLM returns tool call</strong><br>The model decides to use a tool: "call web_search with query='AI trends 2026'"</div></div>
<div class="step"><div class="step-num">6</div><div class="step-text"><strong>Kernel gates the tool</strong> <span class="tag tag-green">governance</span><br>Before the tool runs: is it in the allowed list? Within budget? → ALLOW or DENY</div></div>
<div class="step"><div class="step-num">7</div><div class="step-text"><strong>Tool executes</strong><br>If allowed: tool runs and returns result. If denied: error returned to LLM, it adapts.</div></div>
<div class="step"><div class="step-num">8</div><div class="step-text"><strong>Usage recorded</strong> <span class="tag tag-green">governance</span><br>Tokens, cost, tool calls → process table. Heartbeat sent. Audit log updated.</div></div>
</div>

<h2>Where Governance Happens (per platform)</h2>
<table>
  <tr><th>Step</th><th><span class="tag tag-green">ForgeOS</span></th><th><span class="tag tag-blue">ADK</span></th><th><span class="tag tag-purple">Claude SDK</span></th><th><span class="tag tag-orange">Managed</span></th></tr>
  <tr><td>3. Pre-check</td><td>In-process</td><td>HTTP to kernel</td><td>HTTP to kernel</td><td>At session creation</td></tr>
  <tr><td>6. Tool gate</td><td>runtime.check_tool()</td><td>Wrapper function</td><td>PreToolUse hook</td><td>No interception</td></tr>
  <tr><td>8. Usage</td><td>Automatic</td><td>HTTP POST /usage</td><td>HTTP POST /usage</td><td>Read from session</td></tr>
  <tr><td>Latency</td><td>~0.1ms</td><td>~50ms</td><td>~50ms</td><td>N/A</td></tr>
  <tr><td>Control level</td><td>Full</td><td>Full</td><td>Full</td><td>Session only</td></tr>
</table>

<h2>Code: Where It Happens</h2>
<div class="grid2">
<div class="card">
<h3>Step 6 — ForgeOS (in-process)</h3>
<pre># src/platform/agentic_loop.py:428
from forgeos_sdk.runtime import runtime
decision = await runtime.check_tool(
    tool_name, tool_input
)
if decision.denied:
    return {"error": "Kernel denied"}
# → Direct Python call, ~0.1ms</pre>
</div>
<div class="card">
<h3>Step 6 — Claude SDK (PreToolUse hook)</h3>
<pre># mode-c-claude-sdk-main.py
async def pre_tool_hook(tool_name, ...):
    resp = await httpx.post(
        f"{FORGEOS_URL}/kernel/check-tool",
        json={"agent_id": ID, "tool_name": tool_name}
    )
    if resp.json()["action"] == "deny":
        return {"permissionDecision": "deny"}
# → HTTP round-trip, ~50ms</pre>
</div>
</div>
""")

# =========================================================================
# Page 5: Fleet Status
# =========================================================================

@app.get("/fleet", response_class=HTMLResponse)
async def fleet():
    return _page("fleet", "Fleet Status",
        f"Live agent data from ForgeOS at <code>{FORGEOS_URL}</code>. Auto-refreshes every 10 seconds.",
        """
<div id="fleet-data"><div class="loading">Loading fleet data...</div></div>
<script>
async function loadFleet(){
  try{
    const r=await fetch('/api/fleet');
    const d=await r.json();
    const s=d.summary||{};
    let html=`<div class="grid4" style="margin-bottom:20px">
      <div class="card"><h3>${s.total||0}</h3><p>Total Agents</p></div>
      <div class="card"><h3 style="color:#10A37F">${s.running||0}</h3><p>Running</p></div>
      <div class="card"><h3 style="color:#f87171">${s.failed||0}</h3><p>Failed</p></div>
      <div class="card"><h3 style="color:#fbbf24">${s.quarantined||0}</h3><p>Quarantined</p></div>
    </div>`;
    html+='<table><tr><th>Agent</th><th>Namespace</th><th>Phase</th><th>$ Spent</th><th>Tokens</th><th>Tool Calls</th><th>Last Heartbeat</th></tr>';
    for(const a of (d.agents||[])){
      const phase=a.phase==='running'?'<span class="badge badge-running">running</span>':a.phase==='failed'?'<span class="badge badge-failed">failed</span>':a.phase;
      const hb=a.last_heartbeat?(new Date(a.last_heartbeat)).toLocaleTimeString():'-';
      html+=`<tr><td><strong>${a.name}</strong></td><td>${a.namespace}</td><td>${phase}</td><td>$${(a.dollars||0).toFixed(4)}</td><td>${a.tokens||0}</td><td>${a.tool_calls||0}</td><td>${hb}</td></tr>`;
    }
    html+='</table>';
    document.getElementById('fleet-data').innerHTML=html;
  }catch(e){document.getElementById('fleet-data').innerHTML='<div class="card">Error loading fleet: '+e+'</div>';}
}
loadFleet();
setInterval(loadFleet,10000);
</script>
""")

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
