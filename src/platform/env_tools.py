"""`env__exec` / `bash` tool — run a shell command inside the agent's execution
environment (a Kubernetes pod), gated by the kernel `env.exec` syscall.

Flow: resolve the agent's bound environment → call
`kernel.syscall(verb="env.exec", subject=agent, object=env_id, dispatcher=...)`.
Admission (identity → capability(`check_env_exec`) → quota → policy → boundary)
runs first; only if it passes does the dispatcher run `kubectl exec` in the pod.
The syscall pipeline returns a generic allow (it does not propagate dispatcher
details), so the command output is captured via a closure holder.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "Shell command to run inside the environment pod, e.g. 'ls -la /'",
        }
    },
    "required": ["command"],
}

ENV_TOOL_SCHEMAS = [
    {
        "name": "env__exec",
        "description": (
            "Run a shell command inside this agent's execution environment (a "
            "Kubernetes pod). Kernel-gated. Returns {ok, stdout, stderr, code}."
        ),
        "input_schema": _SCHEMA,
    },
    {
        "name": "bash",
        "description": "Alias for env__exec — run a shell command in the agent's environment pod.",
        "input_schema": _SCHEMA,
    },
]


def make_env_tool_handlers(env_mgr: Any, get_kernel: Any) -> dict[str, Any]:
    """Build the async handlers for `env__exec`/`bash`, bound to the
    EnvironmentManager. ``get_kernel`` is a zero-arg callable returning the
    kernel (read lazily — the kernel is attached to the executor after the
    handlers are registered)."""

    async def _handle(tool_input: dict | None, agent_context: dict | None) -> dict[str, Any]:
        command = ((tool_input or {}).get("command") or "").strip()
        ctx = agent_context or {}
        agent_id = ctx.get("agent_id")
        if not command:
            return {"success": False, "error": "command is required"}
        if not agent_id:
            return {"success": False, "error": "no agent_id in context"}
        if env_mgr is None:
            return {"success": False, "error": "environments are not enabled on this server"}
        env_id = env_mgr.bound_env_id(agent_id)
        if not env_id:
            return {"success": False, "error": "no execution environment is bound to this agent"}
        kernel = get_kernel() if callable(get_kernel) else get_kernel
        if kernel is None:
            return {"success": False, "error": "kernel not available"}

        holder: dict[str, Any] = {}

        def _dispatch(syscall):
            # Runs only after admission stages pass. Sync (kubectl subprocess).
            from src.platform.kernel import KernelDecision
            holder["out"] = env_mgr.exec_sync(agent_id, command)
            return KernelDecision.allow(reason="env.exec dispatched")

        try:
            decision = await asyncio.to_thread(
                kernel.syscall,
                verb="env.exec",
                subject=agent_id,
                object=env_id,
                args={"command": command, "capability_token": ctx.get("capability_token")},
                dispatcher=_dispatch,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("env.exec syscall failed")
            return {"success": False, "error": f"env.exec failed: {e}"}

        if getattr(decision, "action", "deny") != "allow":
            return {
                "success": False,
                "error": f"Kernel denied env.exec: {getattr(decision, 'reason', '')}",
                "decision_action": getattr(decision, "action", "deny"),
            }
        return {"success": True, "result": holder.get("out") or {"ok": False, "stderr": "no output"}}

    return {"env__exec": _handle, "bash": _handle}
