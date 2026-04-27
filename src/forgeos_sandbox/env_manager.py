"""
Environment Manager — runs inside a K8s pod, manages multiple agents.

Exposes a lightweight HTTP API on port 8080 that the platform uses to
start/stop agents dynamically. Each agent runs as an asyncio task sharing
the pod's filesystem and resources.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections import deque
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s | env-mgr | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("env_manager")

ENV_ID = os.environ.get("ENV_ID", "")
MANAGER_PORT = int(os.environ.get("MANAGER_PORT", "8080"))


class AgentHandle:
    """Tracks a running agent inside this environment."""

    def __init__(self, agent_id: str, config: dict):
        self.agent_id = agent_id
        self.config = config
        self.task: asyncio.Task | None = None
        self.runner: Any = None
        self.status = "starting"
        self.started_at = time.time()
        self.log_buffer: deque[str] = deque(maxlen=5000)


class PerAgentLogHandler(logging.Handler):
    """Captures log lines into an agent's deque buffer."""

    def __init__(self, buffer: deque):
        super().__init__()
        self._buffer = buffer

    def emit(self, record):
        try:
            self._buffer.append(self.format(record))
        except Exception:
            pass


class EnvironmentManager:
    """Manages multiple agents as asyncio tasks inside a single pod."""

    def __init__(self):
        self._agents: dict[str, AgentHandle] = {}
        self._env_id = ENV_ID

    async def start_agent(self, config: dict) -> dict:
        agent_id = config.get("agent_id", "")
        if not agent_id:
            return {"error": "Missing agent_id"}
        if agent_id in self._agents:
            return {"error": f"Agent {agent_id} already running"}

        handle = AgentHandle(agent_id, config)

        agent_logger = logging.getLogger(f"forgeos_sandbox.agent.{agent_id}")
        agent_logger.setLevel(logging.INFO)
        handler = PerAgentLogHandler(handle.log_buffer)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"))
        agent_logger.addHandler(handler)

        from src.forgeos_sandbox.runner import SandboxRunner
        runner = SandboxRunner(config)
        handle.runner = runner
        handle.status = "running"
        handle.task = asyncio.create_task(self._run_agent(handle, runner))
        self._agents[agent_id] = handle

        logger.info("Agent started: %s (model=%s, loop=%s)", agent_id, config.get("model"), config.get("loop_mode"))
        return {"agent_id": agent_id, "status": "started"}

    async def _run_agent(self, handle: AgentHandle, runner):
        try:
            await runner.run_async()
            handle.status = "completed"
        except asyncio.CancelledError:
            handle.status = "stopped"
        except Exception as e:
            handle.status = "failed"
            logger.error("Agent %s failed: %s", handle.agent_id, e)

    async def stop_agent(self, agent_id: str) -> dict:
        handle = self._agents.get(agent_id)
        if not handle:
            return {"error": f"Agent {agent_id} not found"}

        if handle.runner:
            handle.runner.stop()
        if handle.task and not handle.task.done():
            handle.task.cancel()
            try:
                await asyncio.wait_for(handle.task, timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        handle.status = "stopped"
        logger.info("Agent stopped: %s", agent_id)
        return {"agent_id": agent_id, "status": "stopped"}

    def list_agents(self) -> list[dict]:
        return [
            {
                "agent_id": h.agent_id,
                "status": h.status,
                "started_at": h.started_at,
                "model": h.config.get("model", ""),
                "loop_mode": h.config.get("loop_mode", False),
            }
            for h in self._agents.values()
        ]

    def get_agent_status(self, agent_id: str) -> dict:
        handle = self._agents.get(agent_id)
        if not handle:
            return {"error": "not found"}
        return {
            "agent_id": agent_id,
            "status": handle.status,
            "started_at": handle.started_at,
            "uptime_seconds": round(time.time() - handle.started_at, 1),
        }

    def get_agent_logs(self, agent_id: str, tail: int = 200) -> dict:
        handle = self._agents.get(agent_id)
        if not handle:
            return {"agent_id": agent_id, "logs": "", "status": "not found"}
        lines = list(handle.log_buffer)
        if tail and len(lines) > tail:
            lines = lines[-tail:]
        return {
            "agent_id": agent_id,
            "logs": "\n".join(lines),
            "status": handle.status,
        }

    async def remove_agent(self, agent_id: str) -> dict:
        result = await self.stop_agent(agent_id)
        self._agents.pop(agent_id, None)
        return result


async def handle_request(manager: EnvironmentManager, method: str, path: str, body: bytes) -> tuple[int, dict]:
    """Route HTTP requests to manager methods."""
    if path == "/healthz" and method == "GET":
        return 200, {"status": "ok", "env_id": ENV_ID, "agents": len(manager._agents)}

    if path == "/agents" and method == "GET":
        return 200, {"agents": manager.list_agents()}

    if path == "/agents/start" and method == "POST":
        config = json.loads(body) if body else {}
        result = await manager.start_agent(config)
        code = 200 if "error" not in result else 400
        return code, result

    if path.startswith("/agents/") and path.endswith("/stop") and method == "POST":
        agent_id = path.split("/")[2]
        result = await manager.stop_agent(agent_id)
        code = 200 if "error" not in result else 404
        return code, result

    if path.startswith("/agents/") and path.endswith("/logs") and method == "GET":
        agent_id = path.split("/")[2]
        return 200, manager.get_agent_logs(agent_id)

    if path.startswith("/agents/") and path.endswith("/status") and method == "GET":
        agent_id = path.split("/")[2]
        result = manager.get_agent_status(agent_id)
        code = 200 if "error" not in result else 404
        return code, result

    if path.startswith("/agents/") and method == "DELETE":
        agent_id = path.split("/")[2]
        result = await manager.remove_agent(agent_id)
        code = 200 if "error" not in result else 404
        return code, result

    return 404, {"error": "not found"}


async def run_server(manager: EnvironmentManager, port: int):
    """Minimal asyncio HTTP server — no external deps needed."""

    async def client_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                writer.close()
                return

            parts = request_line.decode().strip().split()
            if len(parts) < 2:
                writer.close()
                return

            method, path = parts[0], parts[1]

            headers = {}
            while True:
                line = await reader.readline()
                if line == b"\r\n" or not line:
                    break
                key, _, val = line.decode().partition(":")
                headers[key.strip().lower()] = val.strip()

            body = b""
            content_length = int(headers.get("content-length", 0))
            if content_length:
                body = await reader.readexactly(content_length)

            status, data = await handle_request(manager, method, path, body)
            response_body = json.dumps(data).encode()
            status_text = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "Error")

            writer.write(f"HTTP/1.1 {status} {status_text}\r\n".encode())
            writer.write(b"Content-Type: application/json\r\n")
            writer.write(f"Content-Length: {len(response_body)}\r\n".encode())
            writer.write(b"\r\n")
            writer.write(response_body)
            await writer.drain()
        except Exception as e:
            logger.debug("Request error: %s", e)
        finally:
            writer.close()

    server = await asyncio.start_server(client_handler, "0.0.0.0", port)
    logger.info("Environment manager listening on port %d (env_id=%s)", port, ENV_ID)
    async with server:
        await server.serve_forever()


async def main_async():
    manager = EnvironmentManager()
    await run_server(manager, MANAGER_PORT)


def main():
    if not ENV_ID:
        from src.forgeos_sandbox.runner import main as runner_main
        runner_main()
        return
    logger.info("Starting environment manager for %s", ENV_ID)
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
