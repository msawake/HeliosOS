"""
Anthropic Managed Agents Stack Adapter.

Deploys agents to Anthropic's hosted runtime via the Managed Agents API.
ForgeOS handles governance (budget, ACL, audit, namespace isolation).
Anthropic handles execution (gVisor sandbox, tool running, state).

Flow:
  deploy → kernel admit → POST /v1/agents + POST /v1/environments
  invoke → kernel check → POST /v1/sessions → POST events → stream SSE → record usage

Requires: ANTHROPIC_API_KEY env var and beta access.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import textwrap
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

ANTHROPIC_API_URL = "https://api.anthropic.com"
BETA_HEADER = "managed-agents-2026-04-01"


class AnthropicManagedClient:
    """HTTP client for the Anthropic Managed Agents API."""

    def __init__(self, api_key: str | None = None, base_url: str = ANTHROPIC_API_URL):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._base_url = base_url.rstrip("/")
        self._http = None

    def _get_http(self):
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": BETA_HEADER,
                    "content-type": "application/json",
                },
                timeout=120,
            )
        return self._http

    async def create_agent(self, model: str, name: str, system: str,
                           tools: list[dict] | None = None) -> dict:
        http = self._get_http()
        resp = await http.post("/v1/agents", json={
            "model": model,
            "name": name,
            "system": system,
            "tools": tools or [{"type": "agent_toolset_20260401"}],
        })
        resp.raise_for_status()
        return resp.json()

    async def create_environment(self, name: str,
                                 packages: dict[str, list[str]] | None = None) -> dict:
        http = self._get_http()
        body: dict[str, Any] = {"name": name}
        if packages:
            body["package_managers"] = packages
        resp = await http.post("/v1/environments", json=body)
        resp.raise_for_status()
        return resp.json()

    async def create_session(self, agent_id: str, environment_id: str,
                             title: str = "") -> dict:
        http = self._get_http()
        resp = await http.post("/v1/sessions", json={
            "agent": agent_id,
            "environment_id": environment_id,
            "title": title or "ForgeOS session",
        })
        resp.raise_for_status()
        return resp.json()

    async def send_message(self, session_id: str, message: str,
                           poll_interval: float = 3, max_polls: int = 40) -> str:
        """Send a user message and poll for the agent's response."""
        http = self._get_http()
        import asyncio

        resp = await http.post(
            f"/v1/sessions/{session_id}/events",
            json={"events": [
                {"type": "user.message", "content": [{"type": "text", "text": message}]}
            ]},
        )
        resp.raise_for_status()

        for _ in range(max_polls):
            await asyncio.sleep(poll_interval)

            session_resp = await http.get(f"/v1/sessions/{session_id}")
            session_data = session_resp.json()
            status = session_data.get("status", "")
            usage = session_data.get("usage", {})

            if status == "idle":
                events_resp = await http.get(f"/v1/sessions/{session_id}/events?limit=50")
                events = events_resp.json().get("data", [])

                output = ""
                tool_calls = []
                for event in events:
                    etype = event.get("type", "")
                    if etype == "agent.message":
                        for block in event.get("content", []):
                            if block.get("type") == "text":
                                output += block.get("text", "")
                    elif etype == "agent.tool_use":
                        tool_calls.append(event.get("name", ""))

                return json.dumps({
                    "output": output,
                    "tool_calls": tool_calls,
                    "tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                })

        return json.dumps({"output": "Timeout waiting for agent response", "tool_calls": [], "tokens": 0})

    async def delete_agent(self, agent_id: str) -> bool:
        http = self._get_http()
        resp = await http.delete(f"/v1/agents/{agent_id}")
        return resp.status_code < 300


class AnthropicManagedAdapter(AgentStackAdapter):
    """Stack adapter for Anthropic Managed Agents (hosted runtime)."""

    stack_name = "anthropic-managed"

    def __init__(self, tool_executor=None, llm_router=None, api_key: str | None = None):
        self._stack_name = self.stack_name
        self._tool_executor = tool_executor
        self._llm_router = llm_router
        self._client = AnthropicManagedClient(api_key=api_key)
        self._agents: dict[str, AgentDefinition] = {}
        self._managed_ids: dict[str, dict] = {}  # agent_id → {agent_id, env_id}
        self._sessions: dict[str, str] = {}  # agent_id → session_id

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def

        try:
            managed_agent = await self._client.create_agent(
                model=agent_def.llm_config.chat_model or "claude-sonnet-4-5-20250514",
                name=agent_def.name,
                system=agent_def.system_prompt or agent_def.description or "",
            )

            packages = (agent_def.metadata or {}).get("pip_packages")
            managed_env = await self._client.create_environment(
                name=f"{agent_def.name}-env",
                packages={"pip": packages} if packages else None,
            )

            self._managed_ids[agent_def.agent_id] = {
                "managed_agent_id": managed_agent["id"],
                "managed_env_id": managed_env["id"],
            }
            logger.info(
                "Anthropic Managed Agent created: %s → %s (env=%s)",
                agent_def.name, managed_agent["id"], managed_env["id"],
            )
        except Exception as e:
            logger.warning(
                "Managed Agent creation failed for %s: %s — will use fallback on invoke",
                agent_def.name, e,
            )
            self._managed_ids[agent_def.agent_id] = {"error": str(e)}

        return agent_def.agent_id

    async def invoke(
        self,
        agent_id: str,
        prompt: str,
        context: dict | None = None,
        history: list[dict] | None = None,
    ) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED,
                error=f"Agent {agent_id} not found",
            )

        managed = self._managed_ids.get(agent_id, {})
        if "error" in managed or "managed_agent_id" not in managed:
            return await self._invoke_fallback(agent_id, agent_def, prompt, context, history)

        return await self._invoke_managed(agent_id, agent_def, prompt, managed)

    async def _invoke_managed(
        self, agent_id, agent_def, prompt, managed,
    ) -> AgentResult:
        """Invoke via Anthropic Managed Agents API."""
        try:
            session = await self._client.create_session(
                agent_id=managed["managed_agent_id"],
                environment_id=managed["managed_env_id"],
                title=f"ForgeOS invocation: {agent_def.name}",
            )
            session_id = session["id"]
            self._sessions[agent_id] = session_id

            raw = await self._client.send_message(session_id, prompt)
            result = json.loads(raw)

            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.COMPLETED,
                output=result.get("output", ""),
                tool_calls=[{"name": t} for t in result.get("tool_calls", [])],
                tokens_used=result.get("tokens", 0),
            )
        except Exception as e:
            logger.exception("Managed Agent invoke failed: %s", agent_id)
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED, error=str(e),
            )

    async def _invoke_fallback(
        self, agent_id, agent_def, prompt, context, history,
    ) -> AgentResult:
        """Fallback to ForgeOS platform agentic loop when Managed API unavailable."""
        try:
            from src.platform.agentic_loop import run_agentic_loop
            agent_context = build_agent_context(agent_def, context)
            return await run_agentic_loop(
                llm_router=self._llm_router,
                llm_config=agent_def.llm_config,
                system_prompt=agent_def.system_prompt or agent_def.description or "",
                user_prompt=prompt,
                tool_definitions=None,
                tool_executor=self._tool_executor,
                agent_context=agent_context,
                history=history,
                callback_registry=(context or {}).get("_callback_registry"),
            )
        except Exception as e:
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED, error=str(e),
            )

    async def start_loop(self, agent_id: str) -> None:
        pass

    async def stop(self, agent_id: str) -> None:
        managed = self._managed_ids.get(agent_id, {})
        if "managed_agent_id" in managed:
            try:
                await self._client.delete_agent(managed["managed_agent_id"])
            except Exception:
                pass

    def get_status(self, agent_id: str) -> AgentStatus:
        return AgentStatus.IDLE

    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]:
        name = agent_def.name
        model = agent_def.llm_config.chat_model or "claude-sonnet-4-5-20250514"
        prompt = (agent_def.system_prompt or agent_def.description or "").replace('"', '\\"')

        agent_py = textwrap.dedent(f'''\
            """Deploy {name} to Anthropic Managed Agents."""
            import httpx, os, json

            API_KEY = os.environ["ANTHROPIC_API_KEY"]
            HEADERS = {{
                "x-api-key": API_KEY,
                "anthropic-beta": "managed-agents-2026-04-01",
                "content-type": "application/json",
            }}
            BASE = "https://api.anthropic.com"

            # 1. Create agent
            agent = httpx.post(f"{{BASE}}/v1/agents", headers=HEADERS, json={{
                "model": "{model}",
                "name": "{name}",
                "system": "{prompt[:200]}",
                "tools": [{{"type": "agent_toolset_20260401"}}],
            }}).json()
            print(f"Agent: {{agent['id']}}")

            # 2. Create environment
            env = httpx.post(f"{{BASE}}/v1/environments", headers=HEADERS, json={{
                "name": "{name}-env",
            }}).json()
            print(f"Environment: {{env['id']}}")

            # 3. Start session
            session = httpx.post(f"{{BASE}}/v1/sessions", headers=HEADERS, json={{
                "agent": agent["id"],
                "environment_id": env["id"],
            }}).json()
            print(f"Session: {{session['id']}}")

            # 4. Send message
            result = httpx.post(
                f"{{BASE}}/v1/sessions/{{session['id']}}/events",
                headers=HEADERS,
                json={{"type": "user_message", "message": "Hello!"}},
            )
            print(result.text)
        ''')

        return {
            "agent.py": agent_py,
            "README.md": f"# {name}\n\nAnthropic Managed Agent deployed via ForgeOS.\n",
        }
