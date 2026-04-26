"""
OpenClaw Stack Adapter.

Manages a real OpenClaw gateway process and communicates via its HTTP REST API
(OpenAI-compatible /v1/chat/completions) and CLI for agent invocation.

The gateway runs as a subprocess on a dedicated port. Each ForgeOS agent maps
to an OpenClaw workspace with SOUL.md, AGENTS.md, and tool configurations.

When the gateway is unavailable, falls back to the platform agentic loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import textwrap
import time
from pathlib import Path
from typing import Any

from stacks.base import (
    AgentDefinition,
    AgentResult,
    AgentStackAdapter,
    AgentStatus,
    OwnershipType,
    build_agent_context,
)

logger = logging.getLogger(__name__)

# Default path to the openclaw2 runtime
OPENCLAW_DIR = os.environ.get(
    "OPENCLAW_DIR",
    str(Path(__file__).resolve().parents[2] / "openclaw2"),
)
OPENCLAW_PORT = int(os.environ.get("OPENCLAW_PORT", "18789"))
OPENCLAW_STATE_DIR = os.environ.get(
    "OPENCLAW_STATE_DIR",
    str(Path.home() / ".openclaw-forgeos"),
)


TOOL_PROXY_PORT = int(os.environ.get("OPENCLAW_TOOL_PROXY_PORT", "18790"))


class ToolProxyServer:
    """Local HTTP server that proxies OpenClaw tool calls through the kernel.

    The OpenClaw Node.js gateway calls ``POST http://127.0.0.1:{port}/tool``
    for every tool invocation. This server validates the agent token, checks
    permissions via the kernel, executes the tool via ``tool_executor``, and
    returns the result.

    Reuses ``SandboxTokenStore`` for token minting/validation.
    """

    def __init__(self, tool_executor, port: int = TOOL_PROXY_PORT):
        self._tool_executor = tool_executor
        self.port = port
        self._runner: Any | None = None
        self._site: Any | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> bool:
        try:
            from aiohttp import web
        except ImportError:
            try:
                return await self._start_uvicorn()
            except Exception:
                logger.warning("ToolProxyServer: no aiohttp or uvicorn — proxy disabled")
                return False

        app = web.Application()
        app.router.add_post("/tool", self._handle_tool)
        app.router.add_get("/health", self._handle_health)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await self._site.start()
        logger.info("OpenClaw tool proxy started on http://127.0.0.1:%d", self.port)
        return True

    async def _start_uvicorn(self) -> bool:
        """Fallback: use FastAPI/uvicorn if aiohttp is not installed."""
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        import uvicorn

        proxy_app = FastAPI()

        @proxy_app.post("/tool")
        async def tool_endpoint(request: Request):
            return await self._handle_fastapi_tool(request)

        @proxy_app.get("/health")
        async def health():
            return {"status": "ok"}

        config = uvicorn.Config(proxy_app, host="127.0.0.1", port=self.port, log_level="warning")
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())
        await asyncio.sleep(0.3)
        logger.info("OpenClaw tool proxy started (uvicorn) on http://127.0.0.1:%d", self.port)
        return True

    async def _handle_health(self, request=None):
        from aiohttp import web
        return web.json_response({"status": "ok"})

    async def _handle_tool(self, request):
        """aiohttp handler: validate token, check kernel, execute tool."""
        from aiohttp import web
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        token = request.headers.get("X-Agent-Token", "")
        return web.json_response(await self._process_tool_call(body, token))

    async def _handle_fastapi_tool(self, request):
        """FastAPI handler: validate token, check kernel, execute tool."""
        from fastapi.responses import JSONResponse
        body = await request.json()
        token = request.headers.get("x-agent-token", "")
        result = await self._process_tool_call(body, token)
        return JSONResponse(result)

    async def _process_tool_call(self, body: dict, token: str) -> dict:
        """Core logic shared by both aiohttp and FastAPI handlers."""
        from stacks.sandbox.adapter import get_token_store

        claims = get_token_store().verify(token)
        if not claims:
            return {"error": "Invalid or expired agent token"}

        tool_name = body.get("tool_name", "")
        tool_input = body.get("tool_input", {})
        agent_id = claims["agent_id"]

        # Kernel permission check via runtime
        try:
            from src.forgeos_sdk.runtime import runtime as _rt
            if _rt.is_registered:
                rt_token = _rt.bind(agent_id, namespace=claims.get("namespace", "default"))
                try:
                    decision = await _rt.check_tool(tool_name, tool_input)
                    if decision.denied:
                        logger.warning("Proxy denied %s for %s: %s", tool_name, agent_id, decision.reason)
                        return {"error": f"Kernel denied: {decision.reason}"}
                    if hasattr(decision, "action") and decision.action == "rate_limit":
                        return {"error": f"Rate limited: {decision.reason}"}
                finally:
                    _rt.unbind(rt_token)
        except Exception as e:
            logger.error("Proxy kernel check failed for %s: %s", tool_name, e)
            return {"error": f"Kernel check failed: {e}"}

        # Execute tool
        if not self._tool_executor:
            return {"error": "No tool executor available"}

        ctx = {
            "agent_id": agent_id,
            "namespace": claims.get("namespace", "default"),
            "tier": claims.get("tier", 3),
        }
        try:
            result = await self._tool_executor.execute(tool_name, tool_input, ctx)
            return result if isinstance(result, dict) else {"result": str(result)}
        except Exception as e:
            return {"error": str(e)}

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None


class OpenClawGateway:
    """Manages the OpenClaw gateway subprocess lifecycle."""

    def __init__(self, openclaw_dir: str = OPENCLAW_DIR, port: int = OPENCLAW_PORT):
        self.openclaw_dir = Path(openclaw_dir)
        self.port = port
        self._process: asyncio.subprocess.Process | None = None
        self._ready = False
        self._auth_token: str = ""

    @property
    def available(self) -> bool:
        """Check if the openclaw2 runtime exists on disk."""
        return (self.openclaw_dir / "openclaw.mjs").exists()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> bool:
        """Start the OpenClaw gateway as a subprocess."""
        if self.running:
            return True

        if not self.available:
            logger.warning("OpenClaw runtime not found at %s", self.openclaw_dir)
            return False

        state_dir = Path(OPENCLAW_STATE_DIR)
        state_dir.mkdir(parents=True, exist_ok=True)

        env = {
            **os.environ,
            "OPENCLAW_STATE_DIR": str(state_dir),
            "NODE_ENV": "production",
        }

        try:
            self._process = await asyncio.create_subprocess_exec(
                "node", str(self.openclaw_dir / "openclaw.mjs"),
                "gateway",
                "--port", str(self.port),
                "--bind", "loopback",
                "--auth", "none",
                cwd=str(self.openclaw_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info("OpenClaw gateway starting on port %d (pid %d)", self.port, self._process.pid)

            # Wait for gateway to become ready
            self._ready = await self._wait_for_ready(timeout=30)
            if self._ready:
                logger.info("OpenClaw gateway ready on http://127.0.0.1:%d", self.port)
            else:
                logger.error("OpenClaw gateway failed to start within 30s")
                await self.stop()
            return self._ready

        except Exception as e:
            logger.error("Failed to start OpenClaw gateway: %s", e)
            return False

    async def _wait_for_ready(self, timeout: float = 30) -> bool:
        """Poll the health endpoint until the gateway is ready."""
        import httpx
        deadline = time.time() + timeout
        async with httpx.AsyncClient(timeout=2) as client:
            while time.time() < deadline:
                try:
                    resp = await client.get(f"{self.base_url}/health")
                    if resp.status_code == 200:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        return False

    async def stop(self):
        """Stop the gateway subprocess."""
        if self._process and self._process.returncode is None:
            logger.info("Stopping OpenClaw gateway (pid %d)", self._process.pid)
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            self._process = None
            self._ready = False

    async def chat(self, message: str, agent_id: str = "main", system_prompt: str | None = None) -> dict:
        """Invoke an agent turn via the OpenClaw CLI (WebSocket RPC to gateway).

        OpenClaw's gateway is WebSocket-based, not REST. The CLI command
        ``node openclaw.mjs agent --agent {id} --message "..." --json``
        sends a message through the gateway and returns the response as JSON.

        When --local is used, the agent runs embedded with API keys from the
        shell environment (no gateway needed).
        """
        import json as _json

        args = [
            "node", str(self.openclaw_dir / "openclaw.mjs"),
            "agent",
            "--agent", agent_id,
            "--message", message,
            "--json",
        ]
        # If gateway is running, route through it; otherwise run locally
        if not self.running:
            args.append("--local")

        env = {**os.environ, "OPENCLAW_STATE_DIR": str(Path(OPENCLAW_STATE_DIR))}

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(self.openclaw_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            output_text = stdout.decode().strip()

            # Parse JSON output from --json flag
            try:
                result = _json.loads(output_text)
                payloads = result.get("payloads", [])
                text = " ".join(p.get("text", "") for p in payloads if p.get("text"))
                meta = result.get("meta", {})
                agent_meta = meta.get("agentMeta", {})
                usage = agent_meta.get("lastCallUsage", {})
                tokens = usage.get("total", 0)
                return {
                    "text": text,
                    "tokens": tokens,
                    "model": agent_meta.get("model", ""),
                    "duration_ms": meta.get("durationMs", 0),
                    "session_id": agent_meta.get("sessionId", ""),
                }
            except _json.JSONDecodeError:
                # Non-JSON output — return raw text
                return {"text": output_text, "tokens": 0}
        except asyncio.TimeoutError:
            if proc and proc.returncode is None:
                proc.kill()
            return {"error": "OpenClaw agent timed out after 300s"}
        except Exception as e:
            return {"error": str(e)}

    async def invoke_agent(self, message: str, agent_id: str = "main") -> str:
        """Invoke an agent turn via the CLI (fire-and-forget style)."""
        if not self.available:
            return "[OpenClaw not available]"

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "node", str(self.openclaw_dir / "openclaw.mjs"),
                "agent",
                "--message", message,
                cwd=str(self.openclaw_dir),
                env={
                    **os.environ,
                    "OPENCLAW_STATE_DIR": str(Path(OPENCLAW_STATE_DIR)),
                },
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            output = stdout.decode().strip()
            if proc.returncode != 0:
                err = stderr.decode().strip()
                logger.error("OpenClaw agent error: %s", err)
                return output or f"[Error: {err[:200]}]"
            return output
        except asyncio.TimeoutError:
            # Kill the subprocess to prevent leaks
            if proc and proc.returncode is None:
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    logger.error("Failed to kill timed-out OpenClaw process (pid=%s)", proc.pid)
            return "[OpenClaw agent timed out after 300s]"
        except Exception as e:
            return f"[OpenClaw error: {e}]"
        finally:
            # Ensure process is cleaned up
            if proc and proc.returncode is None:
                proc.kill()


class OpenClawAdapter(AgentStackAdapter):
    """
    Real OpenClaw stack adapter.

    Manages a live OpenClaw gateway subprocess and routes agent invocations
    through its HTTP API. Falls back to the platform agentic loop when the
    gateway is unavailable.
    """

    stack_name = "openclaw"

    def __init__(self, llm_router=None, tool_executor=None, openclaw_dir: str = OPENCLAW_DIR):
        self._llm_router = llm_router
        self._tool_executor = tool_executor
        self._agents: dict[str, AgentDefinition] = {}
        self._loops: dict[str, asyncio.Task] = {}
        self._agent_tokens: dict[str, str] = {}
        self._gateway = OpenClawGateway(openclaw_dir=openclaw_dir)
        self._gateway_lock = asyncio.Lock()  # prevents double gateway start
        self._tool_proxy = ToolProxyServer(tool_executor=tool_executor)
        self._proxy_started = False

    async def _ensure_gateway(self) -> bool:
        """Start the gateway if not already running. Retries on crash.
        Serialized via lock to prevent double-start."""
        if self._gateway.running:
            return True
        if not self._gateway.available:
            return False
        async with self._gateway_lock:
            if self._gateway.running:
                return True
            # Start tool proxy alongside the gateway
            if not self._proxy_started:
                try:
                    self._proxy_started = await self._tool_proxy.start()
                except Exception as e:
                    logger.warning("Tool proxy failed to start: %s", e)
            return await self._gateway.start()

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def

        # Mint a scoped token for kernel-gated tool calls
        from stacks.sandbox.adapter import get_token_store
        token = get_token_store().mint(agent_def)
        self._agent_tokens[agent_def.agent_id] = token

        self._setup_workspace(agent_def)
        logger.info("OpenClaw agent created: %s (%s) [token minted]", agent_def.name, agent_def.agent_id)
        return agent_def.agent_id

    def _setup_workspace(self, agent_def: AgentDefinition):
        """Write SOUL.md and workspace files into the OpenClaw state directory."""
        workspace = Path(OPENCLAW_STATE_DIR) / "workspaces" / agent_def.name
        workspace.mkdir(parents=True, exist_ok=True)

        proxy_url = self._tool_proxy.base_url
        agent_token = self._agent_tokens.get(agent_def.agent_id, "")

        soul = agent_def.system_prompt or (
            f"You are {agent_def.name}.\n\n"
            f"{agent_def.description or agent_def.goal or 'Assist effectively.'}\n\n"
            f"## Rules\n"
            f"- Think step by step using ReAct: Think → Act → Observe → Repeat\n"
            f"- Never guess — confirm before external actions\n"
            f"- Log decisions to memory\n"
            f"- Respect rate limits and budgets\n"
        )
        if agent_def.tools:
            soul += (
                f"\n## Tool Usage\n"
                f"To call a tool, POST to {proxy_url}/tool with:\n"
                f'  {{"tool_name": "<name>", "tool_input": {{...}}}}\n'
                f"  Header: X-Agent-Token: {agent_token}\n"
                f"All tool calls are validated by the ForgeOS kernel.\n"
            )
        (workspace / "SOUL.md").write_text(soul)

        agents_md = (
            f"# {agent_def.name}\n\n"
            f"Department: {agent_def.department or 'general'}\n"
            f"Owner: {agent_def.owner_id or 'platform'}\n"
            f"Execution: {agent_def.execution_type.value}\n"
        )
        if agent_def.tools:
            agents_md += "\n## Available Tools\n" + "\n".join(f"- {t}" for t in agent_def.tools) + "\n"
        (workspace / "AGENTS.md").write_text(agents_md)

        # Write SKILLS with real proxy endpoints
        skills_dir = workspace / "SKILLS"
        skills_dir.mkdir(exist_ok=True)
        skills_yaml = ""
        for tool_name in (agent_def.tools or []):
            skills_yaml += (
                f"- name: {tool_name}\n"
                f"  trigger: \"use {tool_name}\"\n"
                f"  description: \"Calls {tool_name} via ForgeOS kernel proxy\"\n"
                f"  method: POST\n"
                f"  endpoint: \"{proxy_url}/tool\"\n"
                f"  headers:\n"
                f"    X-Agent-Token: \"{agent_token}\"\n"
                f"  body:\n"
                f"    tool_name: \"{tool_name}\"\n"
                f"    tool_input: \"{{{{params}}}}\"\n\n"
            )
        (skills_dir / "default.yaml").write_text(skills_yaml or "# No tools configured\n")

        if agent_def.schedule:
            heartbeat = f"# Heartbeat\n\nSchedule: {agent_def.schedule}\n"
            if agent_def.event_triggers:
                heartbeat += "\nEvent triggers:\n" + "\n".join(f"- {t}" for t in agent_def.event_triggers) + "\n"
            (workspace / "HEARTBEAT.md").write_text(heartbeat)

        (workspace / "memory.md").write_text(f"# Memory for {agent_def.name}\n\n")
        logger.info("OpenClaw workspace written: %s", workspace)

    async def invoke(self, agent_id: str, prompt: str, context: dict | None = None, history: list[dict] | None = None) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        start_time = time.time()

        # Try real OpenClaw gateway first
        gateway_ok = await self._ensure_gateway()
        if gateway_ok:
            result = await self._invoke_via_gateway(agent_def, prompt)
            result.agent_id = agent_id
            result.elapsed_ms = (time.time() - start_time) * 1000
            return result

        # Fallback to platform agentic loop
        if self._llm_router:
            return await self._invoke_via_platform(agent_id, agent_def, prompt, context, start_time, history=history)

        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.COMPLETED,
            output=f"[SIMULATED - No LLM API key configured] Agent '{agent_def.name}' received: {prompt[:100]}. Configure ANTHROPIC_API_KEY or OPENAI_API_KEY.",
            error="No LLM provider available. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.",
            elapsed_ms=(time.time() - start_time) * 1000,
        )

    async def _invoke_via_gateway(self, agent_def: AgentDefinition, prompt: str) -> AgentResult:
        """Invoke agent through the OpenClaw CLI → gateway WebSocket RPC.

        Tool calls inside the OpenClaw runtime are proxied through the
        ToolProxyServer which validates the agent's token and enforces
        kernel permissions before execution.
        """
        response = await self._gateway.chat(
            message=prompt,
            agent_id=agent_def.name,
        )

        if "error" in response:
            return AgentResult(
                agent_id="",
                status=AgentStatus.FAILED,
                error=f"OpenClaw gateway error: {response['error']}",
            )

        return AgentResult(
            agent_id="",
            status=AgentStatus.COMPLETED,
            output=response.get("text", ""),
            tokens_used=response.get("tokens", 0),
        )

    async def _invoke_via_platform(
        self, agent_id: str, agent_def: AgentDefinition,
        prompt: str, context: dict | None, start_time: float,
        history: list[dict] | None = None,
    ) -> AgentResult:
        """Fallback: invoke through the shared platform agentic loop."""
        from src.platform.agentic_loop import run_agentic_loop, build_tool_definitions
        tools = build_tool_definitions(self._tool_executor, agent_def.tools or None)
        system = (
            f"[SOUL] You are {agent_def.name}.\n{agent_def.description}\n\n"
            f"Use ReAct loop: Think → Act → Observe → Repeat.\n"
            f"Pause and ping on any external action. Log decisions to memory."
        )
        result = await run_agentic_loop(
            llm_router=self._llm_router,
            llm_config=agent_def.llm_config,
            system_prompt=system,
            user_prompt=prompt,
            tool_definitions=tools or None,
            tool_executor=self._tool_executor,
            agent_context=build_agent_context(agent_def, agent_id),
            context=context,
            history=history,
            goal=agent_def.goal,
        )
        result.agent_id = agent_id
        result.elapsed_ms = (time.time() - start_time) * 1000
        return result

    async def start_loop(self, agent_id: str) -> None:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return

        async def _loop():
            interval = agent_def.metadata.get("heartbeat_interval_seconds", 900)
            while True:
                try:
                    await self.invoke(agent_id, f"Heartbeat cycle for {agent_def.name}. "
                                                f"Check pending items. Read HEARTBEAT.md if present.")
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("OpenClaw loop error for %s", agent_id)
                await asyncio.sleep(interval)

        self._loops[agent_id] = asyncio.create_task(_loop(), name=f"openclaw-loop-{agent_id}")

    async def stop(self, agent_id: str) -> None:
        task = self._loops.pop(agent_id, None)
        if task:
            task.cancel()

    async def shutdown(self) -> None:
        """Stop all loops, the gateway, and the tool proxy."""
        for agent_id in list(self._loops):
            await self.stop(agent_id)
        await self._gateway.stop()
        await self._tool_proxy.stop()

    async def recover(self) -> int:
        """Rewrite workspace files for all known agents after boot recovery.

        After the registry reloads agents from persistence, executor.recover()
        calls adapter.create_agent() for each agent (which re-registers them
        in `self._agents` and rewrites workspace files). This method adds
        belt-and-braces: it scans `self._agents` and ensures every workspace
        is present on disk (useful if the state dir was wiped).
        """
        recovered = 0
        for agent_def in list(self._agents.values()):
            try:
                self._setup_workspace(agent_def)
                recovered += 1
            except Exception:
                logger.exception("OpenClaw workspace recovery failed for %s", agent_def.name)
        if recovered:
            logger.info("OpenClaw: recovered %d workspace(s)", recovered)
        return recovered

    def get_status(self, agent_id: str) -> AgentStatus:
        if agent_id in self._loops and not self._loops[agent_id].done():
            return AgentStatus.RUNNING
        if agent_id in self._agents:
            return AgentStatus.IDLE
        return AgentStatus.STOPPED

    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]:
        """Generate OpenClaw workspace files for deployment."""
        schedule_section = ""
        if agent_def.schedule:
            schedule_section = f"\nSchedule: {agent_def.schedule}"

        trigger_section = ""
        if agent_def.event_triggers:
            trigger_section = "\nEvent triggers:\n" + "\n".join(
                f"- {t}" for t in agent_def.event_triggers
            )

        proxy_url = self._tool_proxy.base_url
        tools_yaml = ""
        for tool_name in agent_def.tools:
            tools_yaml += textwrap.dedent(f"""\
                - name: {tool_name}
                  trigger: "use {tool_name}"
                  description: "Calls {tool_name} via ForgeOS kernel proxy"
                  method: POST
                  endpoint: "{proxy_url}/tool"
                  body:
                    tool_name: "{tool_name}"
                    tool_input: "{{{{params}}}}"

            """)

        soul = agent_def.system_prompt or textwrap.dedent(f"""\
            # SOUL

            You are {agent_def.name} — an autonomous OpenClaw agent.

            Goal: {agent_def.goal or agent_def.description or 'Assist the user effectively.'}

            Always think step-by-step. Use ReAct loop.
            Human-in-the-loop: pause and ping on any external send action.

            ## Rules
            - Never guess — always confirm with MCP before external actions
            - Log every decision to MEMORY/
            - Respect rate limits and budgets
        """)

        return {
            "SOUL.md": soul,
            "AGENTS.md": textwrap.dedent(f"""\
                # {agent_def.name}

                Department: {agent_def.department or 'general'}
                Owner: {agent_def.owner_id or 'platform'}
                Execution: {agent_def.execution_type.value}
                Stack: openclaw (OpenClaw gateway)
            """),
            "HEARTBEAT.md": textwrap.dedent(f"""\
                # HEARTBEAT
                {schedule_section or 'Every 15 minutes:'}
                - Run primary task cycle
                - Check for pending items
                - Summarize to user if needed
                {trigger_section}
            """),
            "SKILLS/default.yaml": tools_yaml or textwrap.dedent("""\
                name: default_skill
                trigger: "help"
                description: "Default skill — responds to general queries"
                mcp_endpoint: "/tool/default"
                parameters: {}
            """),
            "MEMORY/long-term.md": textwrap.dedent(f"""\
                # Long-Term Memory for {agent_def.name}

                ## Session History
                (auto-populated by the OpenClaw gateway runtime)

                ## Learned Preferences
                (agent updates this as it learns user patterns)
            """),
            "config.yaml": textwrap.dedent(f"""\
                name: "{agent_def.name}"
                stack: openclaw
                execution_type: {agent_def.execution_type.value}
                ownership: {agent_def.ownership.value}
                heartbeat_interval_seconds: {agent_def.metadata.get('heartbeat_interval_seconds', 900)}
                llm:
                  chat_model: "{agent_def.llm_config.chat_model}"
                  provider: "{agent_def.llm_config.provider}"
                tools: {agent_def.tools!r}
            """),
        }
