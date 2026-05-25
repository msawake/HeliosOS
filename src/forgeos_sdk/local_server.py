# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
Minimal HTTP server in front of the in-process platform.

This is the bridge between the Rust ``forgeos`` CLI (no Python, single
static binary) and the Python platform. It boots a ``PlatformBootstrap``
in-process and exposes a small Starlette app that the Rust client talks
to over HTTP.

Design notes:

* Loopback by default (``127.0.0.1``). Override with ``--host`` if you
  want to expose the platform to other machines (LAN / VPN).
* Bearer-token auth. The token is read from ``$FORGEOS_API_TOKEN`` if
  set, otherwise a random 32-hex-char token is generated and printed on
  stdout the first time the server boots. The token is also written to
  ``~/.forgeos/server.lock`` along with the host + port so the Rust CLI
  can discover a running server with no configuration.
* Endpoints intentionally mirror the shape of the deleted FastAPI app's
  ``/api/platform/...`` paths so any third-party HTTP integrations need
  minimal changes. We expose only the subset the Rust CLI needs.

Run with::

    python -m src.forgeos_sdk.local_server --port 5055
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from pathlib import Path
from typing import Any

from .config_store import config_dir
from .local_runtime import _agent_definition_to_dict, _build_agent_definition

from contextlib import asynccontextmanager

try:  # Optional deps — clear error if missing.
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "forgeos local server requires starlette + uvicorn. "
        "Run: pip install 'starlette>=0.36' 'uvicorn[standard]>=0.30'"
    ) from exc


LOCK_FILENAME = "server.lock"


# ---------------------------------------------------------------------------
# Lockfile (host/port/token discovery for the Rust CLI)
# ---------------------------------------------------------------------------


def lock_path() -> Path:
    return config_dir() / LOCK_FILENAME


def _write_lock(host: str, port: int, token: str) -> None:
    lp = lock_path()
    lp.parent.mkdir(parents=True, exist_ok=True)
    payload = {"host": host, "port": port, "token": token, "pid": os.getpid()}
    lp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        lp.chmod(0o600)
    except PermissionError:
        pass


def _clear_lock() -> None:
    try:
        lock_path().unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


def _bearer_required(handler):
    """Wrap a handler so it 401s without a valid bearer token."""

    async def _wrapped(request: Request) -> Response:
        expected = request.app.state.token
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or header[7:] != expected:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await handler(request)

    return _wrapped


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def health(request: Request) -> JSONResponse:
    # Unauthenticated — used as a liveness probe and by the Rust client
    # to confirm the daemon is up before reading the lockfile token.
    bs = request.app.state.bootstrap
    payload = {
        "status": "ok",
        "mode": "local",
        "company_id": bs.company_id,
        "agents": len(bs.platform_registry.list_all()),
        "stacks": list(bs._adapters.keys()) if hasattr(bs, "_adapters") else [],
    }
    return JSONResponse(payload)


@_bearer_required
async def list_agents(request: Request) -> JSONResponse:
    bs = request.app.state.bootstrap
    return JSONResponse(bs.executor.list_agents())


@_bearer_required
async def get_agent(request: Request) -> Response:
    bs = request.app.state.bootstrap
    agent_id = request.path_params["agent_id"]
    defn = bs.platform_registry.get(agent_id)
    if defn is None:
        return JSONResponse({"detail": "agent not found"}, status_code=404)
    status = bs.platform_registry.get_status(agent_id)
    return JSONResponse(_agent_definition_to_dict(defn, status))


@_bearer_required
async def deploy_agent(request: Request) -> JSONResponse:
    """Deploy an agent from a manifest body.

    Accepts JSON with either:
      - ``{"manifest_yaml": "<raw yaml>"}`` — the CLI reads the file locally
        and posts its text.
      - ``{"manifest_path": "/abs/path"}`` — only honored when the server
        and CLI run on the same machine.
    """
    from .manifest import AgentManifest

    body = await request.json()
    if "manifest_yaml" in body:
        import yaml

        data = yaml.safe_load(body["manifest_yaml"])
        manifest = AgentManifest.from_dict(data)
        base_path = None
    elif "manifest_path" in body:
        path = Path(body["manifest_path"])
        if not path.exists():
            return JSONResponse({"detail": f"path not found: {path}"}, status_code=400)
        if path.suffix in (".yaml", ".yml"):
            manifest = AgentManifest.from_yaml(path)
        elif path.suffix == ".json":
            manifest = AgentManifest.from_json(path)
        else:
            return JSONResponse(
                {"detail": f"unsupported manifest file type: {path.suffix}"},
                status_code=400,
            )
        base_path = path.parent
    else:
        return JSONResponse(
            {"detail": "expected manifest_yaml or manifest_path"}, status_code=400
        )

    bs = request.app.state.bootstrap
    deploy_body = manifest.to_deploy_request(base_path=base_path)
    defn = _build_agent_definition(deploy_body)
    agent_id = await bs.executor.deploy(defn)
    return JSONResponse({"agent_id": agent_id}, status_code=201)


@_bearer_required
async def undeploy_agent(request: Request) -> JSONResponse:
    bs = request.app.state.bootstrap
    agent_id = request.path_params["agent_id"]
    ok = await bs.executor.undeploy(agent_id)
    return JSONResponse({"agent_id": agent_id, "removed": bool(ok)})


@_bearer_required
async def stop_agent(request: Request) -> JSONResponse:
    bs = request.app.state.bootstrap
    agent_id = request.path_params["agent_id"]
    ok = await bs.executor.stop_agent(agent_id)
    return JSONResponse({"agent_id": agent_id, "stopped": bool(ok)})


@_bearer_required
async def invoke_agent(request: Request) -> JSONResponse:
    bs = request.app.state.bootstrap
    agent_id = request.path_params["agent_id"]
    body: dict[str, Any] = await request.json()
    prompt = body.get("prompt", "")
    context = body.get("context") or {}
    result = await bs.executor.invoke(agent_id, prompt, context)
    if not isinstance(result, dict):
        result = {"result": result}
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(token: str) -> Starlette:
    routes = [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/platform/agents", list_agents, methods=["GET"]),
        Route("/api/platform/agents", deploy_agent, methods=["POST"]),
        Route("/api/platform/agents/{agent_id}", get_agent, methods=["GET"]),
        Route("/api/platform/agents/{agent_id}", undeploy_agent, methods=["DELETE"]),
        Route("/api/platform/agents/{agent_id}/stop", stop_agent, methods=["POST"]),
        Route("/api/platform/agents/{agent_id}/invoke", invoke_agent, methods=["POST"]),
    ]

    @asynccontextmanager
    async def lifespan(app: Starlette):
        # Boot on the server's own asyncio loop. This is a separate boot
        # path from local_runtime.get_bootstrap() (which owns a dedicated
        # loop for the synchronous CLI). Endpoints await executor/registry
        # methods on this loop directly.
        from src.bootstrap import PlatformBootstrap, _load_dotenv_from_repo_root

        _load_dotenv_from_repo_root()
        bs = PlatformBootstrap(mode="supervised")
        await bs.boot()
        app.state.bootstrap = bs
        try:
            yield
        finally:
            try:
                await bs.shutdown()
            except Exception:
                pass
            _clear_lock()

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.token = token
    return app


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forgeos-server",
        description="Run the ForgeOS Python platform behind a local HTTP API.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5055, help="Bind port (default: 5055)")
    parser.add_argument(
        "--print-token",
        action="store_true",
        help="Print the bearer token to stdout on startup (default: written to ~/.forgeos/server.lock only)",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("FORGEOS_API_TOKEN") or secrets.token_hex(16)
    _write_lock(args.host, args.port, token)
    if args.print_token:
        print(f"FORGEOS_API_TOKEN={token}")

    app = build_app(token)

    import uvicorn

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        _clear_lock()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
