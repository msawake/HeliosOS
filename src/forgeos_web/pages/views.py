"""Public HTML pages and the Prometheus metrics endpoint.

Ported 1:1 from fastapi_app.py:
  - GET /             dashboard_page          (fastapi_app.py:2490-2500)
  - GET /admin        admin_page              (fastapi_app.py:2502-2504, _admin_html:4803)
  - GET /intelligence intelligence_page       (fastapi_app.py:2506-2510, _intel_html:4845)
  - GET /metrics      prometheus_metrics      (fastapi_app.py:2972-2992)

These are public (FastAPI mounted them with no auth dependency), so DRF auth /
permission classes are disabled. The HTML bodies are byte-identical to the
FastAPI handlers; ``company_name`` comes from the process-global di.AppContext.

Plain ``django.views.View`` subclasses returning ``HttpResponse`` — simplest
fit for handlers that emit raw HTML / Prometheus text rather than JSON.
"""

from __future__ import annotations

import logging

from django.http import HttpResponse
from django.views import View

from src.forgeos_web import di

logger = logging.getLogger(__name__)


def _company_name() -> str:
    ctx = di.try_get_context() or di.AppContext()
    return ctx.company_name


def _admin_html(company_name: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{company_name} - Admin Chat</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#0f172a;color:#e2e8f0;font-family:-apple-system,sans-serif;height:100vh;display:flex;flex-direction:column}}
.header{{background:#1e293b;padding:14px 20px;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:18px;color:#f8fafc}}.header a{{color:#94a3b8;font-size:13px;text-decoration:none}}
.quick{{display:flex;gap:8px;padding:12px 20px;flex-wrap:wrap}}
.qbtn{{background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px}}
.qbtn:hover{{color:#f8fafc;border-color:#475569}}
.chat{{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}}
.msg{{max-width:85%;padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.6;white-space:pre-wrap}}
.msg.user{{background:#3b82f6;color:#fff;align-self:flex-end}}.msg.bot{{background:#1e293b;border:1px solid #334155;align-self:flex-start}}
.input-row{{padding:12px 20px;border-top:1px solid #334155;display:flex;gap:8px}}
textarea{{flex:1;background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:10px;border-radius:8px;resize:none;font-size:14px;font-family:inherit}}
button{{background:#3b82f6;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:600}}
</style></head><body>
<div class="header"><h1>Admin Orchestrator</h1><div><a href="/">Dashboard</a> &bull; <a href="/docs">API Docs</a> &bull; <a href="/intelligence">Intelligence</a></div></div>
<div class="quick">
<button class="qbtn" onclick="send('system status')">System Status</button>
<button class="qbtn" onclick="send('list agents')">List Agents</button>
<button class="qbtn" onclick="send('show pending approvals')">Approvals</button>
<button class="qbtn" onclick="send('list workflows')">Workflows</button>
</div>
<div class="chat" id="chat"></div>
<div class="input-row"><textarea id="inp" rows="2" placeholder="Type a command..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();send()}}"></textarea><button onclick="send()">Send</button></div>
<script>
const chat=document.getElementById('chat'),inp=document.getElementById('inp');
let sid=localStorage.getItem('admin_sid')||('admin-'+Date.now());localStorage.setItem('admin_sid',sid);
function addMsg(text,role){{const d=document.createElement('div');d.className='msg '+role;d.textContent=text;chat.appendChild(d);chat.scrollTop=9999999}}
async function send(text){{
  const msg=text||inp.value.trim();if(!msg)return;inp.value='';addMsg(msg,'user');
  addMsg('Thinking...','bot');
  try{{
    const r=await fetch('/api/admin/chat',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{message:msg,session_id:sid}})}});
    const d=await r.json();chat.lastChild.textContent=d.response||d.error||'No response';
  }}catch(e){{chat.lastChild.textContent='Error: '+e.message}}
  chat.scrollTop=9999999;
}}
</script></body></html>"""


def _intel_html(company_name: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{company_name} - Intelligence</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#0f172a;color:#e2e8f0;font-family:-apple-system,sans-serif;height:100vh;display:flex;flex-direction:column}}
.header{{background:#1e293b;padding:14px 20px;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:18px;color:#f8fafc}}.header a{{color:#94a3b8;font-size:13px;text-decoration:none}}
.quick{{display:flex;gap:8px;padding:12px 20px;flex-wrap:wrap}}
.qbtn{{background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px}}
.qbtn:hover{{color:#f8fafc;border-color:#475569}}
.chat{{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}}
.msg{{max-width:85%;padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.6;white-space:pre-wrap}}
.msg.user{{background:#8b5cf6;color:#fff;align-self:flex-end}}.msg.bot{{background:#1e293b;border:1px solid #334155;align-self:flex-start}}
.input-row{{padding:12px 20px;border-top:1px solid #334155;display:flex;gap:8px}}
textarea{{flex:1;background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:10px;border-radius:8px;resize:none;font-size:14px;font-family:inherit}}
button{{background:#8b5cf6;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:600}}
</style></head><body>
<div class="header"><h1>Intelligence Platform</h1><div><a href="/">Dashboard</a> &bull; <a href="/docs">API Docs</a> &bull; <a href="/admin">Admin</a></div></div>
<div class="quick">
<button class="qbtn" onclick="send('What data types are in the ontology?')">Ontology Schema</button>
<button class="qbtn" onclick="send('Show me all customers')">Customers</button>
<button class="qbtn" onclick="send('Pipeline review')">Pipeline</button>
<button class="qbtn" onclick="send('Which customers are at risk of churning?')">Churn Risk</button>
</div>
<div class="chat" id="chat"></div>
<div class="input-row"><textarea id="inp" rows="2" placeholder="Ask a business question..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();send()}}"></textarea><button onclick="send()">Send</button></div>
<script>
const chat=document.getElementById('chat'),inp=document.getElementById('inp');
let sid=localStorage.getItem('intel_sid')||('intel-'+Date.now());localStorage.setItem('intel_sid',sid);
function addMsg(text,role){{const d=document.createElement('div');d.className='msg '+role;d.textContent=text;chat.appendChild(d);chat.scrollTop=9999999}}
async function send(text){{
  const msg=text||inp.value.trim();if(!msg)return;inp.value='';addMsg(msg,'user');
  addMsg('Analyzing...','bot');
  try{{
    const r=await fetch('/api/intelligence/ask',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{question:msg,session_id:sid}})}});
    const d=await r.json();chat.lastChild.textContent=d.response||d.error||'No response';
  }}catch(e){{chat.lastChild.textContent='Error: '+e.message}}
  chat.scrollTop=9999999;
}}
</script></body></html>"""


class DashboardPageView(View):
    """GET / — dashboard index HTML shell (fastapi_app.py:2490)."""

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        company_name = _company_name()
        html = f"""<!DOCTYPE html><html><head><title>{company_name}</title></head>
        <body style="background:#0f1419;color:#e7e9ea;font-family:sans-serif;padding:40px;text-align:center">
        <h1>{company_name} — Helios OS Platform</h1>
        <p style="color:#8899a6">API running. Use <a href="/docs" style="color:#60a5fa">/docs</a> for Swagger UI.</p>
        <div style="display:flex;gap:16px;justify-content:center;margin:32px">
        <a href="/docs" style="background:#3b82f6;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">API Docs (Swagger)</a>
        <a href="/admin" style="background:#10b981;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">Admin Chat</a>
        <a href="/intelligence" style="background:#8b5cf6;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">Intelligence</a>
        </div></body></html>"""
        return HttpResponse(html, content_type="text/html; charset=utf-8")


class AdminPageView(View):
    """GET /admin — ForgeOS admin chat page HTML (fastapi_app.py:2502).

    NOTE: Django's own admin is mounted at /django-admin/, so this /admin is
    the ForgeOS page and does not collide.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        return HttpResponse(
            _admin_html(_company_name()), content_type="text/html; charset=utf-8"
        )


class IntelligencePageView(View):
    """GET /intelligence — intelligence page HTML (fastapi_app.py:2506).

    404 when the ontology is not enabled, matching the FastAPI handler.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        ctx = di.try_get_context() or di.AppContext()
        if not ctx.ontology:
            return HttpResponse(
                "Intelligence not enabled",
                status=404,
                content_type="text/plain; charset=utf-8",
            )
        return HttpResponse(
            _intel_html(ctx.company_name), content_type="text/html; charset=utf-8"
        )


class MetricsView(View):
    """GET /metrics — Prometheus scrape endpoint (fastapi_app.py:2972).

    Refreshes snapshot gauges before emitting. Returns plain text in the
    Prometheus exposition format. Platform objects come from di.AppContext.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        ctx = di.try_get_context() or di.AppContext()
        try:
            from src.platform.metrics import refresh_platform_gauges, render_prometheus

            refresh_platform_gauges(
                platform_registry=ctx.platform_registry,
                platform_executor=ctx.platform_executor,
                company_system=ctx.company_system,
                workflow_engine=ctx.workflow_engine,
            )
            body, content_type = render_prometheus()
            return HttpResponse(body, content_type=content_type)
        except Exception as e:  # noqa: BLE001
            logger.warning("metrics endpoint failed: %s", e)
            return HttpResponse(b"# metrics unavailable\n", content_type="text/plain")
