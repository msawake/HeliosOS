# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
Local, in-process ForgeOS runtime for CLI use.

Owns a process-singleton `PlatformBootstrap` that is lazily booted on first
call. Replaces the HTTP round-trip the CLI used to make against the
Mission Control FastAPI service; the same Python objects (registry,
executor, scheduler, event_bus) are accessed directly.

Thread model: the singleton holds its own asyncio event loop. CLI callers
are synchronous and use :func:`run` to schedule a coroutine and wait for
the result. There is only one loop per process — it is created on first
:func:`get_bootstrap` call and torn down at interpreter exit.
"""

from __future__ import annotations

import asyncio
import atexit
import threading
from typing import Any, Coroutine, TypeVar

_bootstrap = None
_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()
_T = TypeVar("_T")


def get_bootstrap(*, company_id: str = "leadforge", mode: str = "supervised"):
    """Return the process-singleton PlatformBootstrap, booting on first call.

    Repeated calls return the same instance regardless of arguments — the
    args from the first call win. This matches the CLI's single-process,
    single-user model.
    """
    global _bootstrap, _loop
    with _lock:
        if _bootstrap is not None:
            return _bootstrap

        from src.bootstrap import PlatformBootstrap, _load_dotenv_from_repo_root

        _load_dotenv_from_repo_root()
        _loop = asyncio.new_event_loop()
        bootstrap = PlatformBootstrap(mode=mode, company_id=company_id)
        _loop.run_until_complete(bootstrap.boot())
        _bootstrap = bootstrap
        atexit.register(_shutdown)
        return _bootstrap


def run(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run an awaitable on the singleton event loop and return the result."""
    if _loop is None:
        raise RuntimeError(
            "local_runtime.run() called before get_bootstrap(); "
            "call get_bootstrap() first."
        )
    return _loop.run_until_complete(coro)


def is_booted() -> bool:
    return _bootstrap is not None


class LocalClient:
    """In-process facade matching the subset of ForgeOSClient used by the CLI.

    Each method runs against the singleton PlatformBootstrap's registry and
    executor — no HTTP, no auth, no serialization. Returns plain dicts so
    the CLI's existing pretty-printers work unchanged.
    """

    def __init__(self) -> None:
        self._bootstrap = get_bootstrap()

    # ---- Agent lifecycle ----------------------------------------------

    def deploy(self, manifest, base_path=None) -> str:
        from pathlib import Path
        from .manifest import AgentManifest

        if isinstance(manifest, (str, Path)):
            path = Path(manifest)
            if base_path is None:
                base_path = path.parent
            if path.suffix in (".yaml", ".yml"):
                manifest = AgentManifest.from_yaml(path)
            elif path.suffix == ".json":
                manifest = AgentManifest.from_json(path)
            else:
                raise ValueError(f"Unsupported manifest file type: {path.suffix}")

        body = manifest.to_deploy_request(base_path=base_path)
        defn = _build_agent_definition(body)
        return run(self._bootstrap.executor.deploy(defn))

    def undeploy(self, agent_id: str) -> dict:
        ok = run(self._bootstrap.executor.undeploy(agent_id))
        return {"agent_id": agent_id, "removed": bool(ok)}

    def stop(self, agent_id: str) -> dict:
        ok = run(self._bootstrap.executor.stop_agent(agent_id))
        return {"agent_id": agent_id, "stopped": bool(ok)}

    # ---- Agent invocation ---------------------------------------------

    def invoke(self, agent_id: str, prompt: str, context: dict | None = None) -> dict:
        result = run(self._bootstrap.executor.invoke(agent_id, prompt, context or {}))
        # PlatformExecutor.invoke returns an AgentResult-like dict already.
        return result if isinstance(result, dict) else {"result": result}

    # ---- Agent queries ------------------------------------------------

    def list(self, **filters) -> list[dict]:
        return self._bootstrap.executor.list_agents(**filters)

    def get(self, agent_id: str) -> dict | None:
        defn = self._bootstrap.platform_registry.get(agent_id)
        if defn is None:
            return None
        status = self._bootstrap.platform_registry.get_status(agent_id)
        return _agent_definition_to_dict(defn, status)

    def health(self) -> dict:
        b = self._bootstrap
        return {
            "status": "ok",
            "mode": "local",
            "company_id": b.company_id,
            "agents": len(b.platform_registry.list_all()),
            "stacks": list(b._adapters.keys()) if hasattr(b, "_adapters") else [],
        }

    # ---- Context manager (no-op for parity with ForgeOSClient) --------

    def close(self) -> None:
        # The singleton bootstrap lives until process exit; per-call
        # close() is a no-op so `with LocalClient() as c:` works.
        pass

    def __enter__(self) -> "LocalClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _build_agent_definition(body: dict):
    """Build an AgentDefinition from a deploy-request dict.

    Mirrors src/dashboard/fastapi_app.py:create_agent() so the in-process
    path produces identical AgentDefinitions to the old HTTP path. Keep
    these two in sync until the FastAPI path is deleted in chunk 3.
    """
    from stacks.base import AgentDefinition, LLMConfig, ExecutionType, OwnershipType

    ownership_str = body.get("ownership", "shared")
    client_id = body.get("client_id")
    if client_id:
        ownership = OwnershipType.CLIENT
        owner_id = client_id
    else:
        ownership = OwnershipType(ownership_str)
        owner_id = body.get("owner_id") or None

    llm = body.get("llm") or {}
    chat_model = body.get("chat_model") or llm.get("chat_model") or "gpt-4o"
    provider = body.get("provider") or llm.get("provider") or "openai"

    return AgentDefinition(
        name=body["name"],
        stack=body.get("stack", "forgeos"),
        execution_type=ExecutionType(body.get("execution_type", "event_driven")),
        ownership=ownership,
        owner_id=owner_id,
        department=body.get("department") or None,
        namespace=body.get("namespace") or "default",
        description=body.get("description", ""),
        goal=body.get("goal", ""),
        schedule=body.get("schedule"),
        event_triggers=body.get("event_triggers", []),
        tools=body.get("tools", []),
        metadata=body.get("metadata", {}),
        llm_config=LLMConfig(chat_model=chat_model, provider=provider),
        system_prompt=body.get("system_prompt", ""),
    )


def _agent_definition_to_dict(defn, status=None) -> dict:
    return {
        "agent_id": defn.agent_id,
        "name": defn.name,
        "stack": defn.stack,
        "execution_type": getattr(defn.execution_type, "value", defn.execution_type),
        "ownership": getattr(defn.ownership, "value", defn.ownership),
        "namespace": defn.namespace,
        "department": defn.department,
        "description": defn.description,
        "tools": list(defn.tools),
        "status": getattr(status, "value", status) if status else "unknown",
    }


def _shutdown() -> None:
    global _bootstrap, _loop
    if _bootstrap is None or _loop is None:
        return
    try:
        _loop.run_until_complete(_bootstrap.shutdown())
    except Exception:
        pass
    try:
        _loop.close()
    except Exception:
        pass
    _bootstrap = None
    _loop = None
