"""
Sandbox Stack Adapter — spawns agents in isolated Docker containers.

Each container gets resource limits, network isolation, and a scoped API
token. Tool calls are proxied through the Helios OS API where the Kernel
validates permissions. Falls back to platform agentic loop if Docker
is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from typing import Any

from stacks.base import (
    AgentDefinition, AgentResult, AgentStackAdapter, AgentStatus, build_agent_context,
)

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = os.environ.get("FORGEOS_SANDBOX_IMAGE", "forgeos-sandbox:latest")
SANDBOX_NETWORK = os.environ.get("FORGEOS_SANDBOX_NETWORK", "forgeos-internal")
SANDBOX_MEM = os.environ.get("FORGEOS_SANDBOX_MEM_LIMIT", "256m")
SANDBOX_CPU = int(os.environ.get("FORGEOS_SANDBOX_CPU_QUOTA", "50000"))

try:
    import docker
    HAS_DOCKER = True
except ImportError:
    HAS_DOCKER = False


class SandboxTokenStore:
    """In-memory scoped tokens for sandbox agents."""

    def __init__(self):
        self._tokens: dict[str, dict] = {}

    def mint(self, agent_def: AgentDefinition) -> str:
        token = f"sbx_{secrets.token_urlsafe(32)}"
        self._tokens[token] = {
            "agent_id": agent_def.agent_id,
            "namespace": agent_def.namespace,
            "owner_id": agent_def.owner_id or "",
            "tools": agent_def.tools or [],
            "tier": (agent_def.metadata or {}).get("_tier", 3),
            "created_at": time.time(),
        }
        return token

    def mint_for(self, agent_id: str, namespace: str = "default", tools: list[str] | None = None, tier: int = 3) -> str:
        """Mint a scoped token for an externally-spawned agent (e.g. a k8s pod
        that the platform did not launch itself). Used by the dev
        /api/sandbox/register endpoint."""
        token = f"sbx_{secrets.token_urlsafe(32)}"
        self._tokens[token] = {
            "agent_id": agent_id,
            "namespace": namespace,
            "owner_id": "",
            "tools": tools or [],
            "tier": tier,
            "created_at": time.time(),
        }
        return token

    def verify(self, token: str) -> dict | None:
        claims = self._tokens.get(token)
        if not claims:
            return None
        if time.time() - claims["created_at"] > 86400:
            del self._tokens[token]
            return None
        return claims

    def revoke(self, agent_id: str) -> None:
        self._tokens = {t: c for t, c in self._tokens.items() if c["agent_id"] != agent_id}


_token_store = SandboxTokenStore()


def get_token_store() -> SandboxTokenStore:
    return _token_store


class SandboxAdapter(AgentStackAdapter):
    """Spawns agents in isolated Docker containers."""

    stack_name = "sandbox"
    supports_suspend = True  # platform owns the loop -> durable ask_human

    def __init__(self, llm_router=None, tool_executor=None, api_url: str = "http://localhost:5000"):
        self._llm_router = llm_router
        self._tool_executor = tool_executor
        self._api_url = api_url
        self._agents: dict[str, AgentDefinition] = {}
        self._containers: dict[str, Any] = {}
        self._tokens = _token_store
        self._docker = None

        if HAS_DOCKER:
            try:
                self._docker = docker.from_env()
                self._docker.ping()
                logger.info("Sandbox adapter: Docker connected")
                self._ensure_network()
            except Exception as e:
                logger.warning("Sandbox adapter: Docker unavailable (%s) — fallback mode", e)
                self._docker = None

    def _ensure_network(self):
        if not self._docker:
            return
        try:
            self._docker.networks.get(SANDBOX_NETWORK)
        except Exception:
            try:
                self._docker.networks.create(SANDBOX_NETWORK, driver="bridge", internal=True)
                logger.info("Created Docker network: %s", SANDBOX_NETWORK)
            except Exception:
                pass

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def
        logger.info("Sandbox agent registered: %s (%s)", agent_def.name, agent_def.agent_id)
        return agent_def.agent_id

    async def invoke(self, agent_id, prompt, context=None, history=None) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        start = time.time()

        if self._docker:
            return await self._invoke_in_container(agent_def, prompt, start)

        if self._llm_router:
            return await self._invoke_via_platform(agent_id, agent_def, prompt, context, start, history)

        return AgentResult(
            agent_id=agent_id, status=AgentStatus.COMPLETED,
            output=f"[SIMULATED] Sandbox agent '{agent_def.name}' received: {prompt[:100]}",
            elapsed_ms=(time.time() - start) * 1000,
        )

    async def _invoke_in_container(self, agent_def, prompt, start) -> AgentResult:
        token = self._tokens.mint(agent_def)
        name = f"forgeos-sbx-{agent_def.agent_id}-{int(time.time()) % 10000}"

        env = {
            "AGENT_ID": agent_def.agent_id,
            "AGENT_TOKEN": token,
            "FORGEOS_API_URL": self._api_url,
            "AGENT_MODEL": agent_def.llm_config.chat_model if agent_def.llm_config else "gpt-4o-mini",
            "AGENT_PROVIDER": agent_def.llm_config.provider if agent_def.llm_config else "openai",
            "AGENT_SYSTEM_PROMPT": agent_def.system_prompt or "",
            "AGENT_TOOLS": json.dumps(agent_def.tools or []),
            "AGENT_PROMPT": prompt,
            "AGENT_MAX_TURNS": str((agent_def.metadata or {}).get("max_turns", 15)),
            "PYTHONUNBUFFERED": "1",
        }
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            if os.environ.get(key):
                env[key] = os.environ[key]

        max_duration = (agent_def.metadata or {}).get("max_duration_seconds", 300)

        try:
            container = self._docker.containers.run(
                image=SANDBOX_IMAGE, name=name, environment=env,
                mem_limit=SANDBOX_MEM, cpu_quota=SANDBOX_CPU,
                detach=True, auto_remove=False, read_only=True,
                labels={"forgeos.agent_id": agent_def.agent_id, "forgeos.owner_id": agent_def.owner_id or "", "forgeos.namespace": agent_def.namespace},
                tmpfs={"/tmp": "size=64M"},
            )
            self._containers[agent_def.agent_id] = container
            logger.info("Sandbox container started: %s (mem=%s)", name, SANDBOX_MEM)

            exit_info = await asyncio.wait_for(asyncio.to_thread(container.wait), timeout=max_duration)
            logs = container.logs(tail=50).decode("utf-8", errors="replace")
            container.remove(force=True)
            self._containers.pop(agent_def.agent_id, None)
            self._tokens.revoke(agent_def.agent_id)

            elapsed = (time.time() - start) * 1000
            code = exit_info.get("StatusCode", -1)

            if code == 0:
                output = self._extract_output(logs)
                return AgentResult(agent_id=agent_def.agent_id, status=AgentStatus.COMPLETED, output=output, elapsed_ms=elapsed)
            return AgentResult(agent_id=agent_def.agent_id, status=AgentStatus.FAILED, error=f"Exit code {code}", output=logs[-500:], elapsed_ms=elapsed)

        except asyncio.TimeoutError:
            logger.warning("Sandbox timed out: %s", name)
            try:
                container.stop(timeout=5)
                container.remove(force=True)
            except Exception:
                pass
            self._containers.pop(agent_def.agent_id, None)
            self._tokens.revoke(agent_def.agent_id)
            return AgentResult(agent_id=agent_def.agent_id, status=AgentStatus.FAILED, error=f"Timeout after {max_duration}s", elapsed_ms=(time.time() - start) * 1000)

        except Exception as e:
            logger.error("Sandbox error: %s", e)
            self._tokens.revoke(agent_def.agent_id)
            return AgentResult(agent_id=agent_def.agent_id, status=AgentStatus.FAILED, error=str(e), elapsed_ms=(time.time() - start) * 1000)

    async def _invoke_via_platform(self, agent_id, agent_def, prompt, context, start, history=None):
        from src.platform.agentic_loop import run_agentic_loop, build_tool_definitions
        tools = build_tool_definitions(self._tool_executor, agent_def.tools or None)
        result = await run_agentic_loop(
            llm_router=self._llm_router, llm_config=agent_def.llm_config,
            system_prompt=agent_def.system_prompt or f"You are {agent_def.name}.",
            user_prompt=prompt, tool_definitions=tools or None,
            tool_executor=self._tool_executor, agent_context=build_agent_context(agent_def, agent_id),
            context=context, history=history,
                callback_registry=(context or {}).get("_callback_registry"),
        )
        result.agent_id = agent_id
        result.elapsed_ms = (time.time() - start) * 1000
        return result

    async def start_loop(self, agent_id):
        agent_def = self._agents.get(agent_id)
        if not agent_def or not self._docker:
            return
        token = self._tokens.mint(agent_def)
        name = f"forgeos-sbx-{agent_id}"
        env = {
            "AGENT_ID": agent_id, "AGENT_TOKEN": token, "FORGEOS_API_URL": self._api_url,
            "AGENT_MODEL": agent_def.llm_config.chat_model if agent_def.llm_config else "gpt-4o-mini",
            "AGENT_PROVIDER": agent_def.llm_config.provider if agent_def.llm_config else "openai",
            "AGENT_SYSTEM_PROMPT": agent_def.system_prompt or "", "AGENT_TOOLS": json.dumps(agent_def.tools or []),
            "AGENT_PROMPT": agent_def.goal or f"Heartbeat for {agent_def.name}.", "AGENT_MAX_TURNS": "50", "PYTHONUNBUFFERED": "1",
        }
        for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            if os.environ.get(k):
                env[k] = os.environ[k]
        try:
            c = self._docker.containers.run(image=SANDBOX_IMAGE, name=name, environment=env, mem_limit=SANDBOX_MEM, cpu_quota=SANDBOX_CPU, detach=True, auto_remove=True, read_only=True, restart_policy={"Name": "on-failure", "MaximumRetryCount": 3}, tmpfs={"/tmp": "size=64M"})
            self._containers[agent_id] = c
            logger.info("Sandbox always-on started: %s", name)
        except Exception as e:
            logger.error("Sandbox loop failed: %s", e)

    async def stop(self, agent_id):
        c = self._containers.pop(agent_id, None)
        if c:
            try:
                c.stop(timeout=10)
                c.remove(force=True)
            except Exception:
                pass
        self._tokens.revoke(agent_id)

    async def shutdown(self):
        for aid in list(self._containers):
            await self.stop(aid)

    def get_status(self, agent_id):
        c = self._containers.get(agent_id)
        if c:
            try:
                c.reload()
                if c.status == "running":
                    return AgentStatus.RUNNING
            except Exception:
                pass
        return AgentStatus.IDLE if agent_id in self._agents else AgentStatus.STOPPED

    def scaffold_files(self, agent_def):
        return {"sandbox.json": json.dumps({"agent_id": agent_def.agent_id, "image": SANDBOX_IMAGE, "mem": SANDBOX_MEM}, indent=2)}

    @staticmethod
    def _extract_output(logs):
        lines = [ln for ln in logs.strip().split("\n") if ln.strip()]
        for line in reversed(lines):
            if "Done in" in line or "output=" in line.lower():
                return line
        return "\n".join(lines[-10:]) if lines else ""
