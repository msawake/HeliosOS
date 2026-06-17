"""
Helios OS agent-base runtime (M1) — long-running per-agent HTTP server.

This is the process that runs inside a per-agent Kubernetes pod
(`pulumi/components/agent_base.py`). Unlike the one-shot
`src/forgeos_sandbox/runner.py`, it stays alive and serves:

  GET  /healthz  -> 200 (liveness/readiness)
  POST /invoke   -> {"prompt": "..."} runs one agentic loop and returns the result

The agent's identity/config comes from env (set by the Deployment), the *prompt*
comes per request — so the platform dispatches to the pod over HTTP
(`http://<agent>.<ns>/invoke`) instead of invoking in-process. The agentic loop
itself + tool-proxy + Gemini path are reused from `SandboxRunner`, so behavior is
identical to the sandbox path we already proved.

Env (set by the Deployment): AGENT_ID, AGENT_TOKEN, FORGEOS_API_URL, AGENT_MODEL,
AGENT_PROVIDER, AGENT_SYSTEM_PROMPT, AGENT_TOOLS (JSON), AGENT_MAX_TURNS,
FORGEOS_AGENT_PORT (default 8080). Uses only stdlib http.server so the agent-base
image stays minimal (httpx/anthropic/openai already present).
"""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.forgeos_sandbox.runner import SandboxRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s | agent-base | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("agent_runtime")

PORT = int(os.environ.get("FORGEOS_AGENT_PORT", "8080"))


def _maybe_register() -> None:
    """If FORGEOS_REGISTER is set, obtain a scoped sandbox token from the Platform
    API so this pod can proxy its tool calls (e.g. drive__*) back to the platform,
    which holds the service-account credentials. Sets AGENT_TOKEN in the env before
    the runner initializes."""
    import json as _json

    if not os.environ.get("FORGEOS_REGISTER"):
        return
    api = os.environ.get("FORGEOS_API_URL", "").rstrip("/")
    if not api:
        logger.warning("FORGEOS_REGISTER set but no FORGEOS_API_URL — skipping registration")
        return
    try:
        import httpx

        tools = _json.loads(os.environ.get("AGENT_TOOLS", "[]"))
        resp = httpx.post(
            f"{api}/api/sandbox/register",
            json={
                "agent_id": os.environ.get("AGENT_ID", ""),
                "namespace": os.environ.get("AGENT_NAMESPACE", "default"),
                "tools": tools,
            },
            timeout=15,
        )
        resp.raise_for_status()
        os.environ["AGENT_TOKEN"] = resp.json()["token"]
        logger.info("registered with platform %s (%d tools) — tool proxy enabled", api, len(tools))
    except Exception as e:  # noqa: BLE001
        logger.warning("registration failed (%s) — tools will be unavailable", e)


_maybe_register()

# One runner per pod — config is static; the prompt varies per request.
_runner = SandboxRunner()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") in ("/healthz", "/health", ""):
            self._send(200, {"ok": True, "agent_id": _runner.agent_id, "model": _runner.model})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") != "/invoke":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:  # noqa: BLE001
            self._send(400, {"error": f"bad request: {e}"})
            return
        prompt = body.get("prompt") or body.get("task") or ""
        if not prompt:
            self._send(400, {"error": "missing 'prompt'"})
            return
        logger.info("invoke: %.80s", prompt)
        try:
            result = _runner.run(prompt)
            self._send(200, result)
        except Exception as e:  # noqa: BLE001
            logger.exception("invoke failed")
            self._send(500, {"status": "failed", "error": str(e)})

    def log_message(self, *args):  # quiet default access logging
        return


def main():
    logger.info("agent-base runtime up: agent=%s model=%s tools=%d port=%d",
                _runner.agent_id, _runner.model, len(_runner.allowed_tools), PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
